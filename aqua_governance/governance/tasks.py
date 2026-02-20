import logging
import sys
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Optional, Any

from dateutil.parser import parse as date_parse
import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from stellar_sdk import Server
from stellar_sdk.exceptions import NotFoundError

from aqua_governance.governance.exceptions import ClaimableBalanceParsingError, GenerateGrouKeyException
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.parser import generate_vote_key, parse_vote
from aqua_governance.taskapp import app as celery_app
from aqua_governance.utils.requests import load_all_records
from aqua_governance.utils.signals import DisableSignals


logger = logging.getLogger()
UNLOCK_TIMESTAMP_TOLERANCE_SECONDS = 1

@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    """
    Update proposal status, votes and results before the end of voting
    """
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= timezone.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_proposal_results.delay(proposal.id, True)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    """
    Update active proposals
    """
    now = datetime.now()
    active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_results.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    """
    Check expired proposals
    """
    expired_period = datetime.now() - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated_at__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED)

@celery_app.task(ignore_result=True)
def task_update_proposal_results(proposal_id: Optional[int] = None, freezing_amount: bool = False):
    task_update_votes(proposal_id, freezing_amount)
    _update_proposal_final_results(proposal_id)

@celery_app.task(ignore_result=True)
def task_update_votes(proposal_id: Optional[int] = None, freezing_amount: bool = False):
    """
    Update votes for proposal
    """
    if proposal_id is None:
        proposals = Proposal.objects.filter(proposal_status__in=[Proposal.VOTED]).order_by("-id")
    else:
        proposals = Proposal.objects.filter(id=proposal_id)

    horizon_server = Server(settings.HORIZON_URL)

    for proposal in proposals:
        expected_unlock_timestamp = _get_expected_unlock_timestamp(proposal)
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
        if proposal.abstain_issuer:
            request_builders = request_builders + (
                (
                    horizon_server.claimable_balances().for_claimant(proposal.abstain_issuer).order(desc=False),
                    LogVote.VOTE_ABSTAIN,
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
            for claimable_balance in load_all_records(request_builder[0]):
                if not _has_valid_unlock_date(claimable_balance, expected_unlock_timestamp):
                    logger.info(
                        "Skip claimable claimable_balance %s for proposal %s due to invalid abs_before values: %s",
                        claimable_balance.get("id"),
                        proposal.id,
                        _extract_abs_before_values(claimable_balance),
                    )
                    continue
                try:
                    vote_key = generate_vote_key(claimable_balance, proposal, request_builder[1])
                    raw_vote_group = raw_vote_groups.get(vote_key, [])
                    raw_vote_group.append((request_builder[1], claimable_balance))
                    raw_vote_groups.update({vote_key: raw_vote_group})
                except GenerateGrouKeyException:
                    logger.warning('Error generating vote_key', exc_info=sys.exc_info())

        logger.info(f"Proposal {proposal.id} has {len(raw_vote_groups)} vote groups")

        # Sorting raw_vote_group and parse new votes or update old votes
        for vote_key, raw_vote_group in raw_vote_groups.items():
            raw_vote_group.sort(key=lambda item: Decimal(item[1]['amount']), reverse=True)

            votes = list(all_votes.filter(key=vote_key))
            for vote_group_index, (vote_choice, raw_vote) in enumerate(raw_vote_group):
                vote = None
                for _vote in votes:
                    if _vote.group_index == vote_group_index:
                        vote = _vote
                try:
                    if vote is not None:
                        update_vote = _make_updated_vote(vote, vote_group_index, raw_vote, freezing_amount)
                        if update_vote:
                            update_log_vote.append(update_vote)
                            indexed_vote_keys_and_index.append((vote_key, vote_group_index))
                        else:
                            logger.warning(f'Error updating vote for {vote_key}, {vote_group_index}')
                    else:
                        new_vote = _make_new_vote(vote_key, vote_group_index, raw_vote, proposal, vote_choice,
                                                  freezing_amount)
                        old_vote = all_votes.filter(hide=False, claimable_balance_id=new_vote.claimable_balance_id).first()
                        if old_vote is not None:
                            update_vote = _make_updated_vote(old_vote, vote_group_index, raw_vote, freezing_amount)
                            update_log_vote.append(update_vote)
                            indexed_vote_keys_and_index.append((vote_key, vote_group_index))
                        elif new_vote:
                            new_log_vote.append(new_vote)
                            indexed_vote_keys_and_index.append((vote_key, vote_group_index))
                        else:
                            logger.warning(f'Error create vote for {vote_key}, {vote_group_index}')
                except ClaimableBalanceParsingError:
                    logger.warning('Balance info skipped.', exc_info=sys.exc_info())

        # Hiding old voices that are in the database but have not loaded
        for vote in all_votes:
            if (vote.key, vote.group_index) not in indexed_vote_keys_and_index:
                vote.claimed = True
                claimed_log_vote.append(vote)

        LogVote.objects.bulk_create(new_log_vote)
        LogVote.objects.bulk_update(update_log_vote,
                                    ["claimable_balance_id", "amount", "voted_amount", "transaction_link", "claimed"])
        LogVote.objects.bulk_update(claimed_log_vote, ["claimed"])


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()


def _make_new_vote(vote_key: str, vote_group_index: int, claimable_balance: dict, proposal: Proposal, vote_choice: str,
                   freezing_amount: bool):
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
        vote_id=None,
        freezing_amount=freezing_amount
    )


def _make_updated_vote(vote: LogVote, vote_group_index: int, claimable_balance: dict, freezing_amount: bool):
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
        vote_id=vote.id,
        freezing_amount=freezing_amount
    )


def _update_proposal_final_results(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    vote_for_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_FOR, hide=False).values_list('amount', flat=True))
    vote_against_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_AGAINST, hide=False).values_list('amount', flat=True),
    )
    vote_abstain_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_ABSTAIN, hide=False).values_list('amount', flat=True),
    )
    proposal.vote_for_result = vote_for_result
    proposal.vote_against_result = vote_against_result
    proposal.vote_abstain_result = vote_abstain_result

    response = requests.get(settings.AQUA_CIRCULATING_URL)
    if response.status_code == 200:
        proposal.aqua_circulating_supply = response.json()

    response = requests.get(settings.ICE_CIRCULATING_URL)
    if response.status_code == 200:
        proposal.ice_circulating_supply = float(response.json()['ice_supply_amount'])

    with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
        proposal.save(update_fields=['vote_for_result', 'vote_against_result', 'vote_abstain_result',
                                     'aqua_circulating_supply', 'ice_circulating_supply'])


def _extract_abs_before_values(claimable_balance: dict[str, Any]) -> list[str]:
    abs_before_values = []
    for claimant in claimable_balance.get("claimants", []):
        abs_before = claimant.get("predicate", {}).get("not", {}).get("abs_before")
        if abs_before is not None:
            abs_before_values.append(abs_before)
    return abs_before_values


def _get_expected_unlock_timestamp(proposal: Proposal) -> int:
    expected_unlock_date = proposal.end_at + timedelta(hours=1)
    if timezone.is_naive(expected_unlock_date):
        expected_unlock_date = expected_unlock_date.replace(tzinfo=dt_timezone.utc)
    return round(expected_unlock_date.timestamp())


def _parse_abs_before_timestamp(abs_before: Any) -> Optional[int]:
    if abs_before is None:
        return None

    if isinstance(abs_before, str) and abs_before.isdigit():
        return int(abs_before)

    try:
        abs_before_date = date_parse(str(abs_before))
    except (TypeError, ValueError):
        return None

    if abs_before_date is None:
        return None
    if timezone.is_naive(abs_before_date):
        abs_before_date = abs_before_date.replace(tzinfo=dt_timezone.utc)

    return round(abs_before_date.timestamp())


def _parse_epoch_timestamp(epoch_value: Any) -> Optional[int]:
    if epoch_value is None:
        return None

    if isinstance(epoch_value, (int, float)):
        return int(round(float(epoch_value)))

    if isinstance(epoch_value, str):
        epoch_value = epoch_value.strip()
        if not epoch_value:
            return None
        try:
            return int(round(float(epoch_value)))
        except ValueError:
            return None

    return None


def _has_valid_unlock_date(claimable_balance: dict[str, Any], expected_unlock_timestamp: int) -> bool:
    has_abs_before = False
    for claimant in claimable_balance.get("claimants", []):
        predicate_not = claimant.get("predicate", {}).get("not", {})
        abs_before = predicate_not.get("abs_before")
        if abs_before is None:
            continue

        has_abs_before = True
        abs_before_timestamp = _parse_abs_before_timestamp(abs_before)
        if abs_before_timestamp is None:
            return False
        abs_before_epoch = predicate_not.get("abs_before_epoch")
        if abs_before_epoch is not None:
            abs_before_epoch_timestamp = _parse_epoch_timestamp(abs_before_epoch)
            if abs_before_epoch_timestamp is None:
                return False
            if abs(abs_before_timestamp - abs_before_epoch_timestamp) > UNLOCK_TIMESTAMP_TOLERANCE_SECONDS:
                return False
        if abs(abs_before_timestamp - expected_unlock_timestamp) > UNLOCK_TIMESTAMP_TOLERANCE_SECONDS:
            return False

    return has_abs_before
