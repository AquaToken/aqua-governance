import logging
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from stellar_sdk import Server
from stellar_sdk.soroban_rpc import GetTransactionStatus

from aqua_governance.governance.db_locks import acquire_proposal_transition_lock
from aqua_governance.governance.models import AssetToken, Proposal
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


def _start_due_discussion_proposals(now) -> int:
    started_count = 0
    with transaction.atomic():
        acquire_proposal_transition_lock()
        proposals = list(
            Proposal.objects.filter(
                hide=False,
                draft=False,
                action=Proposal.NONE,
                proposal_status=Proposal.DISCUSSION,
                start_at__lte=now,
                end_at__gt=now,
            ).order_by('start_at', 'id'),
        )
        for proposal in proposals:
            locked_proposal = Proposal.objects.select_for_update().get(id=proposal.id)
            if Proposal.has_voting_activation_conflict(
                start_at=locked_proposal.start_at,
                end_at=locked_proposal.end_at,
                current_proposal_id=locked_proposal.id,
            ):
                continue
            locked_proposal.proposal_status = Proposal.VOTING
            locked_proposal.save(update_fields=['proposal_status'])
            started_count += 1
            # The global voting invariant allows only one proposal to be active at a time.
            break
    return started_count


def _finish_due_voting_proposals(now) -> None:
    proposals = Proposal.objects.filter(
        hide=False,
        draft=False,
        proposal_status=Proposal.VOTING,
        end_at__lte=now,
    )
    for proposal in proposals:
        proposal.proposal_status = Proposal.VOTED
        proposal.save(update_fields=['proposal_status'])
        task_update_proposal_results.delay(proposal.id, True)


def _expire_missed_discussion_proposals(now) -> int:
    return Proposal.objects.filter(
        hide=False,
        draft=False,
        action=Proposal.NONE,
        proposal_status=Proposal.DISCUSSION,
        start_at__isnull=False,
        end_at__lte=now,
    ).update(proposal_status=Proposal.EXPIRED)


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
    Update proposal status around the configured voting window.
    """
    proposal = Proposal.objects.get(id=proposal_id)
    now = timezone.now()

    if proposal.proposal_status == Proposal.VOTED:
        return

    if (
        proposal.end_at
        and proposal.end_at <= now + timedelta(seconds=5)
        and proposal.proposal_status == Proposal.VOTING
    ):
        proposal.proposal_status = Proposal.VOTED
        proposal.save(update_fields=['proposal_status'])
        task_update_proposal_results.delay(proposal.id, True)
        return

    if (
        proposal.end_at
        and proposal.end_at <= now
        and proposal.proposal_status == Proposal.DISCUSSION
        and not proposal.draft
        and not proposal.hide
        and proposal.action == Proposal.NONE
    ):
        proposal.proposal_status = Proposal.EXPIRED
        proposal.save(update_fields=['proposal_status'])
        return

    if (
        proposal.start_at
        and proposal.end_at
        and proposal.start_at <= now < proposal.end_at
        and proposal.proposal_status == Proposal.DISCUSSION
        and not proposal.draft
        and not proposal.hide
        and proposal.action == Proposal.NONE
    ):
        with transaction.atomic():
            acquire_proposal_transition_lock()
            locked_proposal = Proposal.objects.select_for_update().get(id=proposal_id)
            if (
                locked_proposal.start_at
                and locked_proposal.end_at
                and locked_proposal.start_at <= now < locked_proposal.end_at
                and locked_proposal.proposal_status == Proposal.DISCUSSION
                and not locked_proposal.draft
                and not locked_proposal.hide
                and locked_proposal.action == Proposal.NONE
                and not Proposal.has_voting_activation_conflict(
                    start_at=locked_proposal.start_at,
                    end_at=locked_proposal.end_at,
                    current_proposal_id=locked_proposal.id,
                )
            ):
                locked_proposal.proposal_status = Proposal.VOTING
                locked_proposal.save(update_fields=['proposal_status'])


@celery_app.task(ignore_result=True)
def task_sync_proposal_statuses_by_time():
    now = timezone.now()
    _finish_due_voting_proposals(now)
    _expire_missed_discussion_proposals(now)
    _start_due_discussion_proposals(now)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    """
    Update active proposals.
    """
    now = timezone.now()
    active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_results.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    """
    Check expired proposals.
    """
    now = timezone.now()
    expired_period = now - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(
        proposal_status=Proposal.DISCUSSION,
        last_updated_at__lte=expired_period,
    ).exclude(
        proposal_type__in=Proposal.ASSET_PROPOSAL_TYPES,
    ).filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
    proposals.update(proposal_status=Proposal.EXPIRED, action=Proposal.NONE)


@celery_app.task(ignore_result=True)
def task_check_pending_proposal_payments():
    proposals = Proposal.objects.filter(
        hide=False,
    ).exclude(action=Proposal.NONE)
    for proposal in proposals:
        proposal.check_transaction()


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

    # select_related on the FK chain — `onchain_action_args` property hits
    # `proposal.asset_payload.asset_token.contract_address`.
    proposal = Proposal.objects.select_related('asset_payload__asset_token').get(id=proposal_id)

    try:
        tx_hash = execute_onchain_action(proposal)
        if not tx_hash:
            raise ValueError('Onchain hook returned empty transaction hash')
    except Exception:
        # X4 stopgap (audit finding 2026-05-21-stage-2-single-shot-findings X4):
        # the failure point may have crossed the RPC broadcast boundary — i.e. the
        # transaction may already be on chain even though our Python frame did not
        # reach the `return tx_hash` line. Clearing `onchain_execution_tx_hash` and
        # setting `FAILED` (the legacy behaviour) made the retry path eligible
        # (`task_retry_failed_onchain_executions` re-enqueues FAILED), enabling
        # silent double-submission.
        #
        # Safer default until the proper durable-idempotency design lands
        # (Stage 3 with OnchainExecution per-attempt table, pre-broadcast vs
        # post-broadcast classification, persistent submission log written
        # BEFORE the RPC call): route to REQUIRES_REVIEW and PRESERVE whatever
        # `onchain_execution_tx_hash` the hook may have written before raising.
        # Operator inspects the row in admin, consults block explorer, then
        # either (a) manually promotes to SUCCESS + clears tx_hash via shell or
        # (b) clears tx_hash and sets status back to PENDING to retry. This
        # trades automation for safety on transient errors that crossed the
        # broadcast boundary.
        logger.exception(
            'Onchain send failed for proposal %s (action=%s); routing to REQUIRES_REVIEW '
            'and preserving any tx_hash the hook may have written before the failure. '
            'See audit finding X4.',
            proposal.id,
            proposal.onchain_action_type,
        )
        # Re-read tx_hash + submitted_at AFTER the failure — the hook may have
        # written them via a side-effect (e.g. inside a stellar-sdk submit
        # callback that persisted to DB before the timeout fired).
        # Intentionally NOT in `update_fields`: tx_hash, submitted_at, started_at.
        Proposal.objects.filter(id=proposal_id).update(
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW,
        )
        return

    Proposal.objects.filter(id=proposal_id).update(
        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUBMITTED,
        onchain_execution_tx_hash=tx_hash,
        onchain_execution_submitted_at=timezone.now(),
        onchain_execution_poll_count=0,
    )


class _MissingAssetPayloadError(Exception):
    """Raised by _sync_asset_token_on_success when an asset proposal has no payload row."""


def _sync_asset_token_on_success(proposal_id: int):
    """Sync canonical AssetToken state after a successful onchain execution.

    MUST be called inside an existing `transaction.atomic()` block; both
    Proposal and AssetToken rows are re-fetched under `select_for_update()` so
    concurrent poll workers and the `AssetTokenAdmin.recompute_whitelisted_from_history`
    admin action serialize on the AssetToken row. Closes audit finding X2.

    Takes `proposal_id: int` (not a `Proposal` instance) by design — forbids
    callers from passing a Python-side stale cache. The fresh row is re-read
    inside this function via `select_for_update`.

    Returns silently for non-asset proposals (NONE action type — nothing to sync).
    Raises `_MissingAssetPayloadError` if proposal IS asset-type but payload row
    is missing — caller treats it as fail-closed and routes proposal to manual review.
    """
    # Re-fetch under row lock on Proposal: guarantees serialization with any
    # other writer touching the same Proposal row (concurrent worker, admin
    # save, etc.). `of=('self',)` constrains FOR UPDATE to the Proposal table
    # only — Postgres rejects FOR UPDATE on the nullable side of a LEFT OUTER
    # JOIN (which is how Django emits the reverse OneToOne to asset_payload).
    # AssetToken is locked separately below.
    proposal = Proposal.objects.select_for_update(of=('self',)).select_related(
        'asset_payload__asset_token',
    ).get(id=proposal_id)

    if not Proposal.is_asset_proposal_type(proposal.proposal_type):
        return
    payload = getattr(proposal, 'asset_payload', None)
    if payload is None:
        raise _MissingAssetPayloadError(
            'AssetProposalPayload missing for asset proposal {}'.format(proposal.id),
        )
    # Lock AssetToken row separately — protects against concurrent
    # `AssetTokenAdmin.recompute_whitelisted_from_history` clobbering our write.
    token = AssetToken.objects.select_for_update().get(pk=payload.asset_token_id)
    now = timezone.now()
    if proposal.proposal_type == Proposal.PROPOSAL_TYPE_ADD_ASSET:
        token.whitelisted = True
        token.whitelisted_since = now
    elif proposal.proposal_type == Proposal.PROPOSAL_TYPE_REMOVE_ASSET:
        token.whitelisted = False
        token.unwhitelisted_since = now
    token.last_execution_at = now
    token.save(update_fields=[
        'whitelisted', 'whitelisted_since', 'unwhitelisted_since',
        'last_execution_at', 'updated_at',
    ])


@celery_app.task(ignore_result=True)
def task_poll_submitted_onchain_executions():
    proposals = Proposal.objects.select_related('asset_payload__asset_token').filter(
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
            next_poll_count = proposal.onchain_execution_poll_count + 1
            update_kwargs = {'onchain_execution_poll_count': next_poll_count}
            if next_poll_count >= settings.ONCHAIN_TX_MAX_POLLS:
                update_kwargs['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW
                logger.error(
                    'Soroban transaction polling failed for proposal %s tx=%s after %s attempts; '
                    'manual review required.',
                    proposal.id,
                    proposal.onchain_execution_tx_hash,
                    next_poll_count,
                )
            Proposal.objects.filter(id=proposal.id).update(**update_kwargs)
            continue

        if result.status == GetTransactionStatus.SUCCESS:
            try:
                with transaction.atomic():
                    # Status-conditional update: only flip SUBMITTED → SUCCESS.
                    # Returns 0 rows if another worker / admin has already moved
                    # this proposal out of SUBMITTED (e.g. into REQUIRES_REVIEW
                    # by retry-task escalation, or another concurrent poll). In
                    # that case we skip the sync — the canonical state has been
                    # set by whoever won the race. Closes audit finding X2.
                    updated = Proposal.objects.filter(
                        id=proposal.id,
                        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUBMITTED,
                    ).update(
                        onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUCCESS,
                    )
                    if updated == 0:
                        logger.info(
                            'Proposal %s no longer SUBMITTED at SUCCESS sync time; '
                            'another worker or admin already transitioned it. Skipping.',
                            proposal.id,
                        )
                    else:
                        _sync_asset_token_on_success(proposal.id)
            except _MissingAssetPayloadError:
                logger.error(
                    'Asset proposal %s reported onchain SUCCESS but has no AssetProposalPayload row; '
                    'routing to manual review without flipping onchain_execution_status.',
                    proposal.id,
                )
                Proposal.objects.filter(id=proposal.id).update(
                    onchain_execution_status=Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW,
                )
            except Exception:
                # SUCCESS update rolled back by the surrounding atomic(); proposal stays SUBMITTED.
                # Reuse the standard poll-count + manual-review escalation so a persistent sync
                # bug can't make this proposal loop forever every tick.
                next_poll_count = proposal.onchain_execution_poll_count + 1
                update_kwargs = {'onchain_execution_poll_count': next_poll_count}
                if next_poll_count >= settings.ONCHAIN_TX_MAX_POLLS:
                    update_kwargs['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW
                    logger.exception(
                        'AssetToken sync after onchain SUCCESS failed for proposal %s after %s attempts; '
                        'manual review required.',
                        proposal.id,
                        next_poll_count,
                    )
                else:
                    logger.exception(
                        'AssetToken sync after onchain SUCCESS failed for proposal %s; '
                        'transaction rolled back, poll_count=%s, will retry on next tick.',
                        proposal.id,
                        next_poll_count,
                    )
                Proposal.objects.filter(id=proposal.id).update(**update_kwargs)
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

    proposals = Proposal.objects.select_related('asset_payload__asset_token').filter(
        proposal_status=Proposal.VOTED,
        proposal_type__in=Proposal.ASSET_PROPOSAL_TYPES,
        onchain_execution_status__in=[
            Proposal.ONCHAIN_EXECUTION_FAILED,
            Proposal.ONCHAIN_EXECUTION_PENDING,
        ],
    ).order_by('-id')

    for proposal in proposals:
        retry_onchain_execution_for_voted_proposal(proposal.id)


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()
