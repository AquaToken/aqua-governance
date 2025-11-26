import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Any

from dateutil.parser import parse as date_parse
import requests
from django.conf import settings
from django.utils import timezone

from stellar_sdk import Server
from stellar_sdk.exceptions import BaseHorizonError, NotFoundError

from aqua_governance.governance.exceptions import ClaimableBalanceParsingError, GenerateGrouKeyException
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.parser import generate_vote_key_by_raw_data, generate_vote_key, parse_vote
from aqua_governance.taskapp import app as celery_app
from aqua_governance.utils.requests import load_all_records
from aqua_governance.utils.signals import DisableSignals

# from aqua_governance.utils.stellar.asset import parse_asset_string

logger = logging.getLogger()


# TODO: old code
# def _parse_claimable_balance(claimable_balance: dict, proposal: Proposal, log_vote: str) -> Optional[LogVote]:
#     AQUA_ASSET = Asset(settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_ISSUER)
#
#     balance_id = claimable_balance['id']
#     asset = parse_asset_string(claimable_balance['asset'])
#     if asset == AQUA_ASSET and LogVote.objects.filter(claimable_balance_id=balance_id).exists():
#         return
#
#     try:
#         return parse_balance_info(claimable_balance, proposal, log_vote)
#     except ClaimableBalanceParsingError:
#         logger.warning('Balance info skipped.', exc_info=sys.exc_info())

# TODO: old code
# @celery_app.task(ignore_result=True)
# def task_update_proposal_result(proposal_id):
#     horizon_server = Server(settings.HORIZON_URL)
#     proposal = Proposal.objects.get(id=proposal_id)
#     new_log_vote_list = []
#
#     # TODO: Rollback asset filter after closing issue. https://github.com/stellar/go/issues/5199
#     request_builders = (
#         (
#             horizon_server.claimable_balances().for_claimant(proposal.vote_for_issuer).order(desc=False),
#             LogVote.VOTE_FOR,
#         ),
#         (
#             horizon_server.claimable_balances().for_claimant(proposal.vote_against_issuer).order(desc=False),
#             LogVote.VOTE_AGAINST,
#         ),
#     )
#
#     for request_builder in request_builders:
#         for balance in load_all_records(request_builder[0]):
#             claimable_balance = _parse_claimable_balance(balance, proposal, request_builder[1])
#             if claimable_balance:
#                 new_log_vote_list.append(claimable_balance)
#
#     proposal.logvote_set.filter(asset_code=settings.GOVERNANCE_ICE_ASSET_CODE, hide=False).delete()
#     proposal.logvote_set.filter(asset_code=settings.GDICE_ASSET_CODE, hide=False).delete()
#     LogVote.objects.bulk_create(new_log_vote_list)
#     _update_proposal_final_results(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= timezone.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_votes.delay(proposal_id)
        _update_proposal_final_results(proposal_id)


# TODO: old code
# @celery_app.task(ignore_result=True)
# def task_update_active_proposals():
#     now = datetime.now()
#     active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)
#
#     for proposal in active_proposals:
#         task_update_proposal_result.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    expired_period = datetime.now() - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated_at__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED)


# TODO: old code
# @celery_app.task(ignore_result=True)
# def task_update_hidden_ice_votes_in_voted_proposals():
#     voted_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTED)
#     horizon_server = Server(settings.HORIZON_URL)
#
#     for proposal in voted_proposals:
#         new_hidden_log_vote_list = []
#         # TODO: Rollback asset filter after closing issue. https://github.com/stellar/go/issues/5199
#         request_builders = (
#             (
#                 horizon_server.claimable_balances().for_claimant(proposal.vote_for_issuer).order(desc=False),
#                 LogVote.VOTE_FOR,
#             ),
#             (
#                 horizon_server.claimable_balances().for_claimant(proposal.vote_against_issuer).order(desc=False),
#                 LogVote.VOTE_AGAINST,
#             ),
#         )
#         for request_builder in request_builders:
#             for balance in load_all_records(request_builder[0]):
#                 try:
#                     claimable_balance = parse_balance_info(balance, proposal, request_builder[1], hide=True)
#                     if claimable_balance:
#                         new_hidden_log_vote_list.append(claimable_balance)
#                 except ClaimableBalanceParsingError:
#                     logger.warning('Balance info skipped.', exc_info=sys.exc_info())
#         proposal.logvote_set.filter(hide=True).delete()
#         LogVote.objects.bulk_create(new_hidden_log_vote_list)


@celery_app.task(ignore_result=True)
def task_update_votes(proposal_id: Optional[int] = None):
    if proposal_id is None:
        proposals = Proposal.objects.filter(
            proposal_status__in=[Proposal.VOTING, Proposal.VOTED, Proposal.EXPIRED]).order_by("-id")
    else:
        proposals = Proposal.objects.filter(id=proposal_id)

    horizon_server = Server(settings.HORIZON_URL)

    for proposal in proposals:
        logger.info(f"Update proposal {proposal.id}")
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

        all_votes = proposal.logvote_set.all()
        raw_vote_groups: dict[str, list[tuple[str, dict[str, Any]]]] = dict()
        new_log_vote: list[LogVote] = []
        claimed_log_vote: list[LogVote] = []
        update_log_vote: list[LogVote] = []
        indexed_vote_keys_and_index: list[tuple[str, int]] = []

        # Making claimable balances groups by vote_key
        for request_builder in request_builders:
            for balance in load_all_records(request_builder[0]):
                try:
                    vote_key = generate_vote_key(balance, proposal, request_builder[1])
                    raw_vote_group = raw_vote_groups.get(vote_key, [])
                    raw_vote_group.append((request_builder[1], balance))
                    raw_vote_groups.update({vote_key: raw_vote_group})
                except GenerateGrouKeyException:
                    logger.warning('Error generating vote_key', exc_info=sys.exc_info())

        logger.info(f"Proposal {proposal.id} has {len(raw_vote_groups)} vote groups")

        # Sorting raw_vote_group and parse new votes or update old votes
        for vote_key, raw_vote_group in raw_vote_groups.items():
            raw_vote_group.sort(key=lambda item: item[1]['amount'], reverse=True)

            votes = all_votes.filter(key=vote_key)
            for vote_group_index, (vote_choice, raw_vote) in enumerate(raw_vote_group):
                vote = votes.filter(group_index=vote_group_index)
                try:
                    if vote.exists():
                        update_vote = _make_updated_vote(vote.get(), vote_group_index, raw_vote)
                        update_log_vote.append(update_vote)
                    else:
                        new_vote = _make_new_vote(vote_key, vote_group_index, raw_vote, proposal, vote_choice)
                        new_log_vote.append(new_vote)
                    indexed_vote_keys_and_index.append((vote_key, vote_group_index))
                except ClaimableBalanceParsingError:
                    logger.warning('Balance info skipped.', exc_info=sys.exc_info())

        # Hiding old voices that are in the database but have not loaded
        for vote in all_votes:
            if (vote.key, vote.group_index) not in indexed_vote_keys_and_index:
                vote.claimed = True
                claimed_log_vote.append(vote)

        LogVote.objects.bulk_create(new_log_vote)
        LogVote.objects.bulk_update(update_log_vote,
                                    ["claimable_balance_id", "current_amount", "transaction_link", "last_update_at"])
        LogVote.objects.bulk_update(claimed_log_vote, ["claimed"])


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()


def _make_new_vote(vote_key: str, vote_group_index: int, claimable_balance: dict, proposal: Proposal, vote_choice: str):
    balance_id = claimable_balance['id']
    original_amount = ""
    created_at = ""
    horizon_server = Server(settings.HORIZON_URL)

    try:
        ops = horizon_server.operations().for_claimable_balance(balance_id).order(desc=False).limit(50).call()
        for record in ops["_embedded"]["records"]:
            if record['type'] == 'create_claimable_balance':
                created_at = str(date_parse(record["created_at"]))
                original_amount = str(record["amount"])
    except NotFoundError:
        original_amount = claimable_balance['amount']
        created_at = claimable_balance['last_modified_time']

    if created_at is None:
        created_at = str(proposal.created_at)

    return parse_vote(
        vote_key=vote_key,
        vote_group_index=vote_group_index,
        claimable_balance=claimable_balance,
        proposal=proposal,
        vote_choice=vote_choice,
        created_at=created_at,
        original_amount=original_amount,
        vote_id=None
    )


def _make_updated_vote(vote: LogVote, vote_group_index: int, claimable_balance: dict):
    created_at = str(vote.created_at)
    original_amount = str(vote.original_amount)

    return parse_vote(
        vote_key=vote.key,
        vote_group_index=vote_group_index,
        claimable_balance=claimable_balance,
        proposal=vote.proposal,
        vote_choice=vote.vote_choice,
        created_at=created_at,
        original_amount=original_amount,
        vote_id=vote.id
    )


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
