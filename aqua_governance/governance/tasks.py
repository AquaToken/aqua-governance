import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

from dateutil.parser import parse as date_parse
import requests
from django.conf import settings
from django.utils import timezone

from stellar_sdk import Asset, Server
from stellar_sdk.exceptions import BaseHorizonError

from aqua_governance.governance.exceptions import ClaimableBalanceParsingError, GenerateGrouKeyException
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.parser import parse_balance_info, generate_vote_key_by_raw_data
from aqua_governance.taskapp import app as celery_app
from aqua_governance.utils.requests import load_all_records
from aqua_governance.utils.signals import DisableSignals
from aqua_governance.utils.stellar.asset import parse_asset_string

logger = logging.getLogger()


def _parse_claimable_balance(claimable_balance: dict, proposal: Proposal, log_vote: str) -> Optional[LogVote]:
    AQUA_ASSET = Asset(settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_ISSUER)

    balance_id = claimable_balance['id']
    asset = parse_asset_string(claimable_balance['asset'])
    if asset == AQUA_ASSET and LogVote.objects.filter(claimable_balance_id=balance_id).exists():
        return

    try:
        return parse_balance_info(claimable_balance, proposal, log_vote)
    except ClaimableBalanceParsingError:
        logger.warning('Balance info skipped.', exc_info=sys.exc_info())


def _update_proposal_final_results(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    vote_for_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_FOR, hide=False).values_list('amount', flat=True))
    vote_against_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_AGAINST, hide=False).values_list('amount', flat=True),
    )
    proposal.vote_for_result = vote_for_result
    proposal.vote_against_result = vote_against_result

    response = requests.get(settings.AQUA_CIRCULATING_URL)
    if response.status_code == 200:
        proposal.aqua_circulating_supply = response.json()

    response = requests.get(settings.ICE_CIRCULATING_URL)
    if response.status_code == 200:
        proposal.ice_circulating_supply = float(response.json()['ice_supply_amount'])

    with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
        proposal.save(update_fields=['vote_for_result', 'vote_against_result', 'aqua_circulating_supply',
                                     'ice_circulating_supply'])


@celery_app.task(ignore_result=True)
def task_update_proposal_result(proposal_id):
    horizon_server = Server(settings.HORIZON_URL)
    proposal = Proposal.objects.get(id=proposal_id)
    new_log_vote_list = []

    # TODO: Rollback asset filter after closing issue. https://github.com/stellar/go/issues/5199
    request_builders = (
        (
            horizon_server.claimable_balances().for_claimant(proposal.vote_for_issuer).order(desc=False),
            LogVote.VOTE_FOR,
        ),
        (
            horizon_server.claimable_balances().for_claimant(proposal.vote_against_issuer).order(desc=False),
            LogVote.VOTE_AGAINST,
        ),
    )

    for request_builder in request_builders:
        for balance in load_all_records(request_builder[0]):
            claimable_balance = _parse_claimable_balance(balance, proposal, request_builder[1])
            if claimable_balance:
                new_log_vote_list.append(claimable_balance)

    proposal.logvote_set.filter(asset_code=settings.GOVERNANCE_ICE_ASSET_CODE, hide=False).delete()
    proposal.logvote_set.filter(asset_code=settings.GDICE_ASSET_CODE, hide=False).delete()
    LogVote.objects.bulk_create(new_log_vote_list)
    _update_proposal_final_results(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= timezone.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_proposal_result.delay(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    now = datetime.now()
    active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_result.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    expired_period = datetime.now() - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated_at__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED)


@celery_app.task(ignore_result=True)
def task_update_hidden_ice_votes_in_voted_proposals():
    voted_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTED)
    horizon_server = Server(settings.HORIZON_URL)

    for proposal in voted_proposals:
        new_hidden_log_vote_list = []
        # TODO: Rollback asset filter after closing issue. https://github.com/stellar/go/issues/5199
        request_builders = (
            (
                horizon_server.claimable_balances().for_claimant(proposal.vote_for_issuer).order(desc=False),
                LogVote.VOTE_FOR,
            ),
            (
                horizon_server.claimable_balances().for_claimant(proposal.vote_against_issuer).order(desc=False),
                LogVote.VOTE_AGAINST,
            ),
        )
        for request_builder in request_builders:
            for balance in load_all_records(request_builder[0]):
                try:
                    claimable_balance = parse_balance_info(balance, proposal, request_builder[1], hide=True)
                    if claimable_balance:
                        new_hidden_log_vote_list.append(claimable_balance)
                except ClaimableBalanceParsingError:
                    logger.warning('Balance info skipped.', exc_info=sys.exc_info())
        proposal.logvote_set.filter(hide=True).delete()
        LogVote.objects.bulk_create(new_hidden_log_vote_list)


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()


@celery_app.task(ignore_result=True)
def update_all_log_votes():
    _load_and_enrichment_votes()
    _normalize_vote_group_index()


def _load_and_enrichment_votes():
    horizon_server = Server(settings.HORIZON_URL)
    log_votes = LogVote.objects.filter(hide=False).order_by("-proposal_id")
    count_log_votes = len(log_votes)

    new_log_vote_list = []
    delete_log_vote_id_list = []
    not_handled_votes_count = 0

    for index, log_vote in enumerate(log_votes):
        start = time.perf_counter()
        new_log_vote = load_claimable_balance_from_operations(horizon_server, log_vote)
        if new_log_vote is None:
            not_handled_votes_count += 1
        else:
            new_log_vote_list.append(new_log_vote)
            delete_log_vote_id_list.append(log_vote.id)
        end = time.perf_counter()

        logger.info(
            f"{index + 1}/{count_log_votes} Indexing log_vote: {log_vote.id}, proposal_id: {log_vote.proposal_id}, time: {end - start:.6f}"
        )
    logger.info("Finish indexing log_votes.")
    logger.info(f"Not handled votes: {not_handled_votes_count}")

    LogVote.objects.filter(id__in=delete_log_vote_id_list).delete()
    LogVote.objects.bulk_create(new_log_vote_list)


def _normalize_vote_group_index():
    logger.info("Normalizing log_vote_group_index.")
    log_votes = LogVote.objects.filter(hide=False).order_by("-proposal_id")
    count_log_votes = len(log_votes)

    for vote_index, vote in enumerate(log_votes):
        start = time.perf_counter()
        if vote.group_index > 0:
            continue
        any_votes = list(LogVote.objects.exclude(id=vote.id).filter(hide=False, key=vote.key))
        if not any_votes:
            continue

        filtered_any_votes = [x for x in any_votes if x.original_amount is not None]
        sorted_vote_group = sorted([vote, *filtered_any_votes], key=lambda x: x.original_amount, reverse=True)
        for index, _vote in enumerate(sorted_vote_group):
            _vote.group_index = index

        LogVote.objects.bulk_update(sorted_vote_group, ["group_index"])
        end = time.perf_counter()
        logger.info(
            f"{vote_index + 1}/{count_log_votes} Normalize log_vote: {vote.id}, proposal_id: {vote.proposal_id}, time: {end - start:.6f}"
        )
    logger.info("Normalizing log_vote_group_index finished.")


def load_claimable_balance_from_operations(horizon_server: Server, log_vote: LogVote) -> Optional[LogVote]:
    vote_claimed = False
    new_log_vote: Optional[LogVote] = None

    try:
        operations = horizon_server.operations().for_claimable_balance(log_vote.claimable_balance_id).call()
        records = operations["_embedded"]["records"]
        for record in records:
            if record['type'] == 'claim_claimable_balance' or record['type'] == 'clawback_claimable_balance':
                vote_claimed = True
            if record['type'] != 'create_claimable_balance':
                continue

            time_list = []
            for claimant in record['claimants']:
                abs_before = claimant.get('predicate', {}).get('not', {}).get('abs_before', None)
                if abs_before is not None:
                    time_list.append(abs_before)

            if not time_list:
                return None

            created_at = date_parse(record['created_at'])
            amount = record['amount']
            sponsor = record['sponsor']
            key = generate_vote_key_by_raw_data(
                log_vote.proposal_id,
                log_vote.vote_choice,
                log_vote.account_issuer,
                log_vote.asset_code,
                time_list
            )

            new_log_vote = LogVote(
                group_index=0,
                key=key,
                id=log_vote.id,
                claimable_balance_id=log_vote.claimable_balance_id,
                proposal=log_vote.proposal,
                vote_choice=log_vote.vote_choice,
                current_amount=log_vote.current_amount,
                original_amount=amount,
                account_issuer=log_vote.account_issuer,
                sponsor=sponsor,
                created_at=created_at,
                last_update_at=log_vote.created_at,
                transaction_link=log_vote.transaction_link,
                asset_code=log_vote.asset_code,
                hide=log_vote.hide,
                time_list=time_list,
                claimed=vote_claimed,
            )
    except BaseHorizonError:
        logger.warning(f"Claimable Balance Load Error: {log_vote.id}", exc_info=sys.exc_info())
    except GenerateGrouKeyException:
        logger.warning(f"Generate Group Key Error: {log_vote.id}", exc_info=sys.exc_info())

    if vote_claimed and new_log_vote is not None:
        new_log_vote.claimed = vote_claimed

    return new_log_vote
