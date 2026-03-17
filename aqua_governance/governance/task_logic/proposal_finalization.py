import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings
from django.db import transaction

from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.utils.signals import DisableSignals


logger = logging.getLogger()


def update_proposal_final_results(proposal_id: int) -> None:
    proposal = Proposal.objects.get(id=proposal_id)
    supported_vote_assets = [settings.GOVERNANCE_ICE_ASSET_CODE, settings.GDICE_ASSET_CODE]
    vote_for_result = sum(
        proposal.logvote_set.filter(
            vote_choice=LogVote.VOTE_FOR,
            hide=False,
            claimed=False,
            asset_code__in=supported_vote_assets,
        ).values_list('amount', flat=True),
    )
    vote_against_result = sum(
        proposal.logvote_set.filter(
            vote_choice=LogVote.VOTE_AGAINST,
            hide=False,
            claimed=False,
            asset_code__in=supported_vote_assets,
        ).values_list('amount', flat=True),
    )
    vote_abstain_result = sum(
        proposal.logvote_set.filter(
            vote_choice=LogVote.VOTE_ABSTAIN,
            hide=False,
            claimed=False,
            asset_code__in=supported_vote_assets,
        ).values_list('amount', flat=True),
    )
    proposal.vote_for_result = vote_for_result
    proposal.vote_against_result = vote_against_result
    proposal.vote_abstain_result = vote_abstain_result

    has_fresh_ice_supply = _update_ice_circulating_supply(proposal)

    with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
        proposal.save(
            update_fields=[
                'vote_for_result',
                'vote_against_result',
                'vote_abstain_result',
                'ice_circulating_supply',
            ],
        )
    _execute_onchain_action_if_needed(proposal, has_fresh_ice_supply=has_fresh_ice_supply)


def retry_onchain_execution_for_voted_proposal(proposal_id: int) -> None:
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.proposal_status != Proposal.VOTED:
        return

    has_fresh_ice_supply = _update_ice_circulating_supply(proposal)
    if has_fresh_ice_supply:
        with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
            proposal.save(update_fields=['ice_circulating_supply'])

    _execute_onchain_action_if_needed(proposal, has_fresh_ice_supply=has_fresh_ice_supply)


def _update_ice_circulating_supply(proposal: Proposal) -> bool:
    try:
        response = requests.get(settings.ICE_CIRCULATING_URL, timeout=10)
    except requests.RequestException:
        logger.exception(
            'Failed to fetch ICE circulating supply for proposal %s.',
            proposal.id,
        )
        return False

    if response.status_code != 200:
        logger.error(
            'ICE supply fetch returned non-200 status for proposal %s: %s',
            proposal.id,
            response.status_code,
        )
        return False

    try:
        proposal.ice_circulating_supply = float(response.json()['ice_supply_amount'])
    except (KeyError, TypeError, ValueError):
        logger.exception(
            'Failed to parse ICE circulating supply payload for proposal %s.',
            proposal.id,
        )
        return False

    return True


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _has_proposal_quorum(proposal: Proposal) -> bool:
    total_votes = (
        _as_decimal(proposal.vote_for_result)
        + _as_decimal(proposal.vote_against_result)
        + _as_decimal(proposal.vote_abstain_result)
    )
    required_votes = (
        _as_decimal(proposal.ice_circulating_supply)
        * _as_decimal(proposal.percent_for_quorum)
        / Decimal('100')
    )
    return total_votes >= required_votes


def _is_proposal_approved(proposal: Proposal) -> bool:
    return _as_decimal(proposal.vote_for_result) > _as_decimal(proposal.vote_against_result)


def _execute_onchain_action_if_needed(proposal: Proposal, has_fresh_ice_supply: bool) -> None:
    should_enqueue_send_task = False

    with transaction.atomic():
        proposal = Proposal.objects.select_for_update().get(id=proposal.id)

        if proposal.onchain_action_type == Proposal.ONCHAIN_ACTION_NONE:
            if (
                proposal.onchain_execution_status != Proposal.ONCHAIN_EXECUTION_NOT_REQUIRED
                or proposal.onchain_execution_tx_hash
                or proposal.onchain_execution_started_at
                or proposal.onchain_execution_submitted_at
                or proposal.onchain_execution_poll_count
            ):
                proposal.onchain_execution_status = Proposal.ONCHAIN_EXECUTION_NOT_REQUIRED
                proposal.onchain_execution_tx_hash = None
                proposal.onchain_execution_started_at = None
                proposal.onchain_execution_submitted_at = None
                proposal.onchain_execution_poll_count = 0
                with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
                    proposal.save(
                        update_fields=[
                            'onchain_execution_status',
                            'onchain_execution_tx_hash',
                            'onchain_execution_started_at',
                            'onchain_execution_submitted_at',
                            'onchain_execution_poll_count',
                        ],
                    )
            return

        if proposal.proposal_status != Proposal.VOTED:
            return

        if proposal.onchain_execution_status == Proposal.ONCHAIN_EXECUTION_SUCCESS:
            return

        if proposal.onchain_execution_status in (
            Proposal.ONCHAIN_EXECUTION_IN_PROGRESS,
            Proposal.ONCHAIN_EXECUTION_SUBMITTED,
            Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW,
        ):
            return

        if not has_fresh_ice_supply:
            logger.error(
                'Skip onchain action for proposal %s due to stale or missing ICE supply.',
                proposal.id,
            )
            proposal.onchain_execution_status = Proposal.ONCHAIN_EXECUTION_FAILED
            proposal.onchain_execution_tx_hash = None
            proposal.onchain_execution_started_at = None
            proposal.onchain_execution_submitted_at = None
            proposal.onchain_execution_poll_count = 0
            with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
                proposal.save(
                    update_fields=[
                        'onchain_execution_status',
                        'onchain_execution_tx_hash',
                        'onchain_execution_started_at',
                        'onchain_execution_submitted_at',
                        'onchain_execution_poll_count',
                    ],
                )
            return

        is_approved = _is_proposal_approved(proposal)
        has_quorum = _has_proposal_quorum(proposal)
        if not is_approved or not has_quorum:
            proposal.onchain_execution_status = Proposal.ONCHAIN_EXECUTION_SKIPPED
            proposal.onchain_execution_tx_hash = None
            proposal.onchain_execution_started_at = None
            proposal.onchain_execution_submitted_at = None
            proposal.onchain_execution_poll_count = 0
            logger.info(
                'Skip onchain action for proposal %s due to result check. approved=%s quorum=%s',
                proposal.id,
                is_approved,
                has_quorum,
            )
            with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
                proposal.save(
                    update_fields=[
                        'onchain_execution_status',
                        'onchain_execution_tx_hash',
                        'onchain_execution_started_at',
                        'onchain_execution_submitted_at',
                        'onchain_execution_poll_count',
                    ],
                )
            return

        proposal.onchain_execution_status = Proposal.ONCHAIN_EXECUTION_PENDING
        proposal.onchain_execution_tx_hash = None
        proposal.onchain_execution_started_at = None
        proposal.onchain_execution_submitted_at = None
        proposal.onchain_execution_poll_count = 0
        with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
            proposal.save(
                update_fields=[
                    'onchain_execution_status',
                    'onchain_execution_tx_hash',
                    'onchain_execution_started_at',
                    'onchain_execution_submitted_at',
                    'onchain_execution_poll_count',
                ],
            )
        should_enqueue_send_task = True

    if should_enqueue_send_task:
        _enqueue_onchain_send_task(proposal.id)


def _enqueue_onchain_send_task(proposal_id: int) -> None:
    from aqua_governance.governance.tasks import task_execute_onchain_action_send

    task_execute_onchain_action_send.delay(proposal_id)
