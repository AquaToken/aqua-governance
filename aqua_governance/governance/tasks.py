import logging
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone
from stellar_sdk import Server
from stellar_sdk.soroban_rpc import GetTransactionStatus

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.onchain_hooks import execute_onchain_action
from aqua_governance.governance.onchain_hooks.soroban import get_soroban_transaction
from aqua_governance.governance.task_logic.proposal_finalization import (
    retry_onchain_execution_for_voted_proposal,
    update_proposal_final_results,
)
from aqua_governance.governance.task_logic.vote_indexing import (
    update_proposal_votes_snapshot,
)
from aqua_governance.taskapp import app as celery_app

logger = logging.getLogger(__name__)


def _mark_stale_in_progress_onchain_executions_for_review() -> None:
    cutoff = timezone.now() - timedelta(seconds=settings.ONCHAIN_EXECUTION_LEASE_SECONDS)
    stale_ids = list(
        Proposal.objects.filter(
            proposal_status=Proposal.VOTED,
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_IN_PROGRESS,
            onchain_execution_tx_hash__isnull=True,
            onchain_execution_started_at__isnull=False,
            onchain_execution_started_at__lt=cutoff,
        ).values_list('id', flat=True),
    )
    if not stale_ids:
        return

    Proposal.objects.filter(id__in=stale_ids).update(
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW,
    )
    logger.error(
        'Marked stale IN_PROGRESS onchain executions for review: proposals=%s',
        stale_ids,
    )


@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    """
    Update proposal status, votes and results before the end of voting.
    """
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= timezone.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_proposal_results.delay(proposal.id, True)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    """
    Update active proposals.
    """
    now = datetime.now()
    active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_results.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    """
    Check expired proposals.
    """
    expired_period = datetime.now() - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated_at__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED)


@celery_app.task(ignore_result=True)
def task_update_proposal_results(proposal_id: int, freezing_amount: bool = False):
    task_update_votes(proposal_id, freezing_amount)
    update_proposal_final_results(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_votes(proposal_id: Optional[int] = None, freezing_amount: bool = False):
    """
    Update votes for proposal.
    """
    if proposal_id is None:
        proposals = Proposal.objects.filter(proposal_status__in=[Proposal.VOTED]).order_by('-id')
    else:
        proposals = Proposal.objects.filter(id=proposal_id)

    horizon_server = Server(settings.HORIZON_URL)

    for proposal in proposals:
        update_proposal_votes_snapshot(
            proposal=proposal,
            horizon_server=horizon_server,
            freezing_amount=freezing_amount,
        )


@celery_app.task(ignore_result=True)
def task_execute_onchain_action_send(proposal_id: int):
    claimed = Proposal.objects.filter(
        id=proposal_id,
        proposal_status=Proposal.VOTED,
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_PENDING,
        onchain_execution_tx_hash__isnull=True,
    ).update(
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_IN_PROGRESS,
        onchain_execution_started_at=timezone.now(),
        onchain_execution_submitted_at=None,
        onchain_execution_poll_count=0,
    )
    if not claimed:
        return

    proposal = Proposal.objects.get(id=proposal_id)

    try:
        tx_hash = execute_onchain_action(proposal)
        if not tx_hash:
            raise ValueError('Onchain hook returned empty transaction hash')
    except Exception:
        logger.exception(
            'Onchain send failed for proposal %s (action=%s).',
            proposal.id,
            proposal.onchain_action_type,
        )
        Proposal.objects.filter(id=proposal_id).update(
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_FAILED,
            onchain_execution_tx_hash=None,
            onchain_execution_submitted_at=None,
            onchain_execution_poll_count=0,
        )
        return

    Proposal.objects.filter(id=proposal_id).update(
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUBMITTED,
        onchain_execution_tx_hash=tx_hash,
        onchain_execution_submitted_at=timezone.now(),
        onchain_execution_poll_count=0,
    )


@celery_app.task(ignore_result=True)
def task_poll_submitted_onchain_executions():
    proposals = Proposal.objects.filter(
        proposal_status=Proposal.VOTED,
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUBMITTED,
    ).exclude(onchain_execution_tx_hash__isnull=True)

    for proposal in proposals:
        try:
            result = get_soroban_transaction(proposal.onchain_execution_tx_hash)
        except Exception:
            logger.exception(
                'Failed to fetch Soroban transaction status for proposal %s tx=%s.',
                proposal.id,
                proposal.onchain_execution_tx_hash,
            )
            continue

        if result.status == GetTransactionStatus.SUCCESS:
            Proposal.objects.filter(id=proposal.id).update(
                onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUCCESS,
            )
            continue

        if result.status == GetTransactionStatus.FAILED:
            logger.error(
                'Soroban transaction failed for proposal %s tx=%s result_xdr=%s',
                proposal.id,
                proposal.onchain_execution_tx_hash,
                getattr(result, 'result_xdr', None),
            )
            Proposal.objects.filter(id=proposal.id).update(
                onchain_execution_status=Proposal.ONCHAIN_EXECUTION_FAILED,
            )
            continue

        next_poll_count = proposal.onchain_execution_poll_count + 1
        update_kwargs = {'onchain_execution_poll_count': next_poll_count}
        if next_poll_count >= settings.ONCHAIN_TX_MAX_POLLS:
            update_kwargs['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW
            logger.error(
                'Soroban transaction remained NOT_FOUND for proposal %s tx=%s after %s polls; '
                'manual review required.',
                proposal.id,
                proposal.onchain_execution_tx_hash,
                next_poll_count,
            )
        Proposal.objects.filter(id=proposal.id).update(**update_kwargs)


@celery_app.task(ignore_result=True)
def task_retry_failed_onchain_executions():
    _mark_stale_in_progress_onchain_executions_for_review()

    proposals = Proposal.objects.filter(
        proposal_status=Proposal.VOTED,
        onchain_action_type__in=[
            Proposal.ONCHAIN_ACTION_ADD_ASSET,
            Proposal.ONCHAIN_ACTION_REMOVE_ASSET,
        ],
        onchain_execution_status__in=[
            Proposal.ONCHAIN_EXECUTION_FAILED,
            Proposal.ONCHAIN_EXECUTION_PENDING,
        ],
    ).order_by('-id')

    for proposal in proposals:
        retry_onchain_execution_for_voted_proposal(proposal.id)


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()
