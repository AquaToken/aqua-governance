import logging
import sys
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Optional, Any

from dateutil.parser import parse as date_parse
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from stellar_sdk import Server
from stellar_sdk.exceptions import NotFoundError

from aqua_governance.governance.claimable_trace import find_origin_claimable_balance_id
from aqua_governance.governance.exceptions import ClaimableBalanceParsingError, GenerateGrouKeyException
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.parser import generate_vote_key, parse_vote
from aqua_governance.taskapp import app as celery_app
from aqua_governance.utils.requests import load_all_records
from aqua_governance.utils.signals import DisableSignals


logger = logging.getLogger()
UNLOCK_TIMESTAMP_TOLERANCE_SECONDS = 1
GROUP_UPDATE_NEW_VOTE = "new_vote"
GROUP_UPDATE_MELTING = "melting"
GROUP_UPDATE_UNCHANGED = "unchanged"
GROUP_UPDATE_AMBIGUOUS = "ambiguous"

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
def task_update_proposal_results(proposal_id: int, freezing_amount: bool = False):
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
        with transaction.atomic():
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

            all_votes = proposal.logvote_set.filter(hide=False)
            raw_vote_groups: dict[str, list[tuple[str, dict[str, Any]]]] = dict()
            new_log_vote: list[LogVote] = []
            update_log_vote: list[LogVote] = []
            processed_vote_ids: set[int] = set()
            origin_cache: dict[str, Optional[str]] = {}

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
                votes = list(all_votes.filter(key=vote_key))
                active_votes = votes
                group_update_type = _classify_vote_group_update(active_votes, raw_vote_group)
                logger.info(
                    "Proposal %s vote_key %s classified as %s (active_votes=%s, current_group_size=%s)",
                    proposal.id,
                    vote_key,
                    group_update_type,
                    len(active_votes),
                    len(raw_vote_group),
                )
                _new_log_vote, _update_log_vote, _processed_vote_ids = _reconcile_vote_group(
                    vote_key=vote_key,
                    raw_vote_group=raw_vote_group,
                    existing_votes=active_votes,
                    all_votes=all_votes,
                    proposal=proposal,
                    freezing_amount=freezing_amount,
                    horizon_server=horizon_server,
                    origin_cache=origin_cache,
                )
                new_log_vote.extend(_new_log_vote)
                update_log_vote.extend(_update_log_vote)
                processed_vote_ids.update(_processed_vote_ids)

            # Mark votes that dropped out of the current grouped snapshot as claimed.
            stale_vote_ids = [
                vote.id for vote in all_votes
                if vote.id is not None and vote.id not in processed_vote_ids and not vote.claimed
            ]

            if stale_vote_ids:
                LogVote.objects.filter(id__in=stale_vote_ids).update(claimed=True)
            LogVote.objects.bulk_create(new_log_vote)
            LogVote.objects.bulk_update(update_log_vote,
                                        ["group_index", "claimable_balance_id", "amount", "voted_amount",
                                         "transaction_link", "claimed"])


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()


def _is_self_sponsored_claimable_balance(claimable_balance: dict[str, Any]) -> bool:
    sponsor = claimable_balance.get("sponsor")
    if not sponsor:
        return False

    for claimant in claimable_balance.get("claimants", []):
        if claimant.get("destination") == sponsor:
            return True

    return False


def _classify_vote_group_update(
    existing_votes: list[LogVote],
    raw_vote_group: list[tuple[str, dict[str, Any]]],
) -> str:
    existing_balance_ids = {
        vote.claimable_balance_id for vote in existing_votes if vote.claimable_balance_id
    }
    raw_votes_by_id = {
        claimable_balance.get("id"): claimable_balance
        for _, claimable_balance in raw_vote_group
        if claimable_balance.get("id")
    }
    current_balance_ids = set(raw_votes_by_id.keys())
    new_balance_ids = current_balance_ids - existing_balance_ids

    if not existing_votes and raw_vote_group:
        return GROUP_UPDATE_NEW_VOTE

    if not new_balance_ids and len(raw_vote_group) == len(existing_votes):
        return GROUP_UPDATE_UNCHANGED

    has_self_sponsored_new_vote = any(
        _is_self_sponsored_claimable_balance(raw_votes_by_id[balance_id]) for balance_id in new_balance_ids
    )
    has_only_service_sponsored_new_balances = bool(new_balance_ids) and all(
        not _is_self_sponsored_claimable_balance(raw_votes_by_id[balance_id]) for balance_id in new_balance_ids
    )

    if len(raw_vote_group) > len(existing_votes) and has_self_sponsored_new_vote:
        return GROUP_UPDATE_NEW_VOTE

    if len(raw_vote_group) <= len(existing_votes) and has_only_service_sponsored_new_balances:
        return GROUP_UPDATE_MELTING

    return GROUP_UPDATE_AMBIGUOUS


def _reconcile_vote_group(
    vote_key: str,
    raw_vote_group: list[tuple[str, dict[str, Any]]],
    existing_votes: list[LogVote],
    all_votes,
    proposal: Proposal,
    freezing_amount: bool,
    horizon_server: Optional[Server] = None,
    origin_cache: Optional[dict[str, Optional[str]]] = None,
) -> tuple[list[LogVote], list[LogVote], set[int]]:
    new_log_vote: list[LogVote] = []
    update_log_vote: list[LogVote] = []
    processed_vote_ids: set[int] = set()
    sorted_raw_vote_group = sorted(raw_vote_group, key=lambda item: Decimal(item[1]['amount']), reverse=True)
    if origin_cache is None:
        origin_cache = {}

    raw_items: list[dict[str, Any]] = []
    for raw_index, (vote_choice, raw_vote) in enumerate(sorted_raw_vote_group):
        raw_items.append(
            {
                "index": raw_index,
                "vote_choice": vote_choice,
                "vote": raw_vote,
                "balance_id": raw_vote.get("id"),
                "self_sponsored": _is_self_sponsored_claimable_balance(raw_vote),
            }
        )

    existing_by_balance_id = {
        vote.claimable_balance_id: vote
        for vote in existing_votes
        if vote.claimable_balance_id is not None
    }
    matched_existing_ids: set[int] = set()
    matched_raw_indexes: set[int] = set()

    def _apply_update(existing_vote: LogVote, raw_item: dict[str, Any]) -> None:
        update_vote = _make_updated_vote(
            existing_vote,
            raw_item["index"],
            raw_item["vote"],
            freezing_amount,
        )
        if update_vote:
            update_log_vote.append(update_vote)
            if update_vote.id is not None:
                processed_vote_ids.add(update_vote.id)
        else:
            logger.warning("Error updating vote for %s, %s", vote_key, raw_item["index"])

    # Phase 1: exact matches by claimable_balance_id
    for raw_item in raw_items:
        balance_id = raw_item["balance_id"]
        existing_vote = existing_by_balance_id.get(balance_id)
        if existing_vote is None:
            continue
        if existing_vote.id is None or existing_vote.id in matched_existing_ids:
            continue
        _apply_update(existing_vote, raw_item)
        matched_existing_ids.add(existing_vote.id)
        matched_raw_indexes.add(raw_item["index"])

    # Phase 2: try origin-chain matching for unresolved service-sponsored replacements
    remaining_existing = [
        vote
        for vote in existing_votes
        if vote.id is not None and vote.id not in matched_existing_ids
    ]
    remaining_raw = [raw_item for raw_item in raw_items if raw_item["index"] not in matched_raw_indexes]
    service_raw = [raw_item for raw_item in remaining_raw if not raw_item["self_sponsored"]]
    if service_raw and remaining_existing and horizon_server is not None:
        origin_matches = _match_unresolved_service_replacements_by_origin(
            horizon_server=horizon_server,
            vote_key=vote_key,
            proposal_id=proposal.id,
            remaining_existing=remaining_existing,
            unresolved_service_raw=service_raw,
            origin_cache=origin_cache,
        )
        for existing_vote, raw_item in origin_matches:
            _apply_update(existing_vote, raw_item)
            if existing_vote.id is not None:
                matched_existing_ids.add(existing_vote.id)
            matched_raw_indexes.add(raw_item["index"])

    # Phase 3: for still-unmatched service-sponsored rows use stable positional fallback
    remaining_existing = [
        vote
        for vote in existing_votes
        if vote.id is not None and vote.id not in matched_existing_ids
    ]
    remaining_raw = [raw_item for raw_item in raw_items if raw_item["index"] not in matched_raw_indexes]
    service_raw = [raw_item for raw_item in remaining_raw if not raw_item["self_sponsored"]]
    if service_raw and remaining_existing:
        remaining_existing.sort(key=lambda vote: vote.group_index)
        service_raw.sort(key=lambda raw_item: raw_item["index"])
        pair_count = min(len(remaining_existing), len(service_raw))

        for index in range(pair_count):
            existing_vote = remaining_existing[index]
            raw_item = service_raw[index]
            _apply_update(existing_vote, raw_item)
            if existing_vote.id is not None:
                matched_existing_ids.add(existing_vote.id)
            matched_raw_indexes.add(raw_item["index"])

    remaining_raw = [raw_item for raw_item in raw_items if raw_item["index"] not in matched_raw_indexes]
    unresolved_service_raw = [raw_item for raw_item in remaining_raw if not raw_item["self_sponsored"]]
    if unresolved_service_raw:
        logger.warning(
            "Proposal %s vote_key %s unresolved service-sponsored replacements (%s items). "
            "Keep existing votes and skip destructive reconciliation for this group.",
            proposal.id,
            vote_key,
            len(unresolved_service_raw),
        )
        _mark_votes_as_processed(existing_votes, processed_vote_ids)
        return new_log_vote, update_log_vote, processed_vote_ids

    # Phase 4: create/reattach unmatched self-sponsored rows (new user votes)
    for raw_item in remaining_raw:
        try:
            new_vote = _make_new_vote(
                vote_key=vote_key,
                vote_group_index=raw_item["index"],
                claimable_balance=raw_item["vote"],
                proposal=proposal,
                vote_choice=raw_item["vote_choice"],
                freezing_amount=freezing_amount,
            )
            if new_vote is None:
                logger.warning("Error create vote for %s, %s", vote_key, raw_item["index"])
                continue

            old_vote = all_votes.filter(claimable_balance_id=new_vote.claimable_balance_id).first()
            if old_vote is not None:
                update_vote = _make_updated_vote(old_vote, raw_item["index"], raw_item["vote"], freezing_amount)
                if update_vote and update_vote.id is not None:
                    update_log_vote.append(update_vote)
                    processed_vote_ids.add(update_vote.id)
                else:
                    logger.warning("Error updating vote for %s, %s", vote_key, raw_item["index"])
                continue

            new_log_vote.append(new_vote)
        except ClaimableBalanceParsingError:
            logger.warning('Balance info skipped.', exc_info=sys.exc_info())

    return new_log_vote, update_log_vote, processed_vote_ids


def _resolve_origin_balance_id(
    horizon_server: Server,
    balance_id: Optional[str],
    origin_cache: dict[str, Optional[str]],
) -> Optional[str]:
    if not balance_id:
        return None
    if balance_id in origin_cache:
        return origin_cache[balance_id]
    try:
        origin_balance_id = find_origin_claimable_balance_id(horizon_server, balance_id)
    except Exception:
        origin_balance_id = None
    origin_cache[balance_id] = origin_balance_id
    return origin_balance_id


def _match_unresolved_service_replacements_by_origin(
    horizon_server: Server,
    vote_key: str,
    proposal_id: int,
    remaining_existing: list[LogVote],
    unresolved_service_raw: list[dict[str, Any]],
    origin_cache: dict[str, Optional[str]],
) -> list[tuple[LogVote, dict[str, Any]]]:
    existing_by_origin: dict[str, list[LogVote]] = {}
    for existing_vote in remaining_existing:
        origin_balance_id = _resolve_origin_balance_id(
            horizon_server=horizon_server,
            balance_id=existing_vote.claimable_balance_id,
            origin_cache=origin_cache,
        )
        if origin_balance_id is None:
            continue
        existing_by_origin.setdefault(origin_balance_id, []).append(existing_vote)

    raw_by_origin: dict[str, list[dict[str, Any]]] = {}
    for raw_item in unresolved_service_raw:
        origin_balance_id = _resolve_origin_balance_id(
            horizon_server=horizon_server,
            balance_id=raw_item.get("balance_id"),
            origin_cache=origin_cache,
        )
        if origin_balance_id is None:
            continue
        raw_by_origin.setdefault(origin_balance_id, []).append(raw_item)

    matched_pairs: list[tuple[LogVote, dict[str, Any]]] = []
    for origin_balance_id, raw_items in raw_by_origin.items():
        existing_items = existing_by_origin.get(origin_balance_id, [])
        if len(raw_items) == 1 and len(existing_items) == 1:
            matched_pairs.append((existing_items[0], raw_items[0]))
            continue
        if raw_items and existing_items:
            logger.warning(
                "Proposal %s vote_key %s ambiguous origin-based match for origin_balance_id=%s "
                "(existing=%s raw=%s)",
                proposal_id,
                vote_key,
                origin_balance_id,
                len(existing_items),
                len(raw_items),
            )

    if matched_pairs:
        logger.info(
            "Proposal %s vote_key %s origin-based matched replacements=%s",
            proposal_id,
            vote_key,
            len(matched_pairs),
        )

    return matched_pairs


def _mark_votes_as_processed(votes: list[LogVote], processed_vote_ids: set[int]) -> None:
    for vote in votes:
        if vote.id is not None:
            processed_vote_ids.add(vote.id)


def _make_new_vote(vote_key: str, vote_group_index: int, claimable_balance: dict, proposal: Proposal, vote_choice: str,
                   freezing_amount: bool):
    balance_id = claimable_balance['id']
    original_amount = None
    created_at = None
    horizon_server = Server(settings.HORIZON_URL)

    try:
        ops = horizon_server.operations().for_claimable_balance(balance_id).order(desc=False).limit(50).call()
        for record in ops["_embedded"]["records"]:
            if record['type'] == 'create_claimable_balance':
                created_at = str(date_parse(record["created_at"]))
                original_amount = str(record["amount"])
    except NotFoundError:
        created_at = claimable_balance['last_modified_time']

    if created_at is None:
        created_at = str(proposal.created_at)

    if original_amount is None:
        original_amount = claimable_balance['amount']

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
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_FOR, hide=False, claimed=False).values_list(
            'amount', flat=True
        )
    )
    vote_against_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_AGAINST, hide=False, claimed=False).values_list(
            'amount', flat=True
        ),
    )
    vote_abstain_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_ABSTAIN, hide=False, claimed=False).values_list(
            'amount', flat=True
        ),
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
