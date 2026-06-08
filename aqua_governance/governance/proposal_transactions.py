import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from aqua_governance.governance.db_locks import acquire_proposal_transition_lock
from aqua_governance.governance.proposal_queue import find_queue_slot_conflict
from aqua_governance.utils.payments import check_proposal_status


logger = logging.getLogger(__name__)


def check_transaction(proposal):
    if proposal.action == proposal.TO_UPDATE:
        return _check_update_transaction(proposal)

    elif proposal.action == proposal.TO_SUBMIT:
        return _check_submit_transaction(proposal)

    elif proposal.action == proposal.TO_CREATE:
        return _check_create_transaction(proposal)

    return None


def _check_update_transaction(proposal):
    status = check_proposal_status(
        proposal.new_transaction_hash,
        proposal.new_text.html,
        settings.PROPOSAL_CREATE_OR_UPDATE_COST,
    )
    if status != proposal.FINE:
        _save_payment_status(proposal, status)
        return

    _history_model(proposal).objects.create(
        version=proposal.version,
        title=proposal.title,
        text=proposal.text,
        transaction_hash=proposal.transaction_hash,
        envelope_xdr=proposal.envelope_xdr,
        proposal=proposal,
        created_at=proposal.last_updated_at,
    )
    proposal.payment_status = status
    proposal.last_updated_at = timezone.now()
    proposal.text = proposal.new_text
    proposal.title = proposal.new_title
    proposal.version = proposal.version + 1
    proposal.transaction_hash = proposal.new_transaction_hash
    proposal.envelope_xdr = proposal.new_envelope_xdr
    proposal.action = proposal.NONE
    proposal.save()


def _check_submit_transaction(proposal):
    status = check_proposal_status(
        proposal.new_transaction_hash,
        proposal.text.html,
        settings.PROPOSAL_SUBMIT_COST,
    )
    if status != proposal.FINE:
        _save_payment_status(proposal, status)
        _log_submit_payment_not_confirmed(proposal, status)
        return {
            'outcome': 'payment_not_confirmed',
            'payment_status': status,
        }

    proposal_model = type(proposal)
    with transaction.atomic():
        acquire_proposal_transition_lock()
        locked_proposal = proposal_model.objects.select_for_update().get(id=proposal.id)
        if locked_proposal.action != proposal.TO_SUBMIT:
            proposal.refresh_from_db()
            return {'outcome': 'skipped'}

        now = timezone.now()
        new_start_at = locked_proposal.new_start_at
        new_end_at = locked_proposal.new_end_at
        if new_start_at is None or new_end_at is None:
            proposal.refresh_from_db()
            return {
                'outcome': 'missing_submit_window',
                'payment_status': status,
            }

        if new_end_at and new_end_at <= now:
            _apply_submit_confirmation(
                proposal,
                locked_proposal,
                status,
                proposal.EXPIRED,
                now,
                create_queue_slot=False,
            )
            proposal.refresh_from_db()
            return {
                'outcome': 'expired',
                'payment_status': status,
            }

        conflict = find_queue_slot_conflict(
            start_at=new_start_at,
            end_at=new_end_at,
            exclude_proposal_id=locked_proposal.id,
        )
        if conflict is not None:
            _mark_submit_slot_conflict(locked_proposal, status, now)
            _log_submit_slot_conflict(locked_proposal, status, conflict)
            proposal.refresh_from_db()
            return {
                'outcome': 'slot_conflict',
                'payment_status': status,
                'conflict': _serialize_queue_conflict(conflict),
            }

        proposal_status = _resolve_submit_proposal_status(proposal, locked_proposal, now)
        try:
            _apply_submit_confirmation(proposal, locked_proposal, status, proposal_status, now)
        except IntegrityError:
            conflict = find_queue_slot_conflict(
                start_at=new_start_at,
                end_at=new_end_at,
                exclude_proposal_id=locked_proposal.id,
            )
            if conflict is not None:
                _mark_submit_slot_conflict(locked_proposal, status, now)
                _log_submit_slot_conflict(locked_proposal, status, conflict)
                proposal.refresh_from_db()
                return {
                    'outcome': 'slot_conflict',
                    'payment_status': status,
                    'conflict': _serialize_queue_conflict(conflict),
                }
            raise

        proposal.refresh_from_db()
        return {
            'outcome': 'booked',
            'payment_status': status,
            'proposal_status': proposal.proposal_status,
        }


def _check_create_transaction(proposal):
    status = check_proposal_status(
        proposal.transaction_hash,
        proposal.text.html,
        settings.PROPOSAL_CREATE_OR_UPDATE_COST,
    )
    if status == proposal.HORIZON_ERROR and proposal.status == proposal.HORIZON_ERROR:
        return

    if proposal.is_asset_proposal and status != proposal.HORIZON_ERROR:
        _apply_asset_create_transaction(proposal, status)
        return

    if status != proposal.HORIZON_ERROR:
        proposal.draft = False
        proposal.action = proposal.NONE
        if status != proposal.FINE:
            proposal.hide = True
    proposal.payment_status = status
    proposal.save()
    return None


def _save_payment_status(proposal, status):
    proposal.payment_status = status
    proposal.save()


def _create_submit_history(source_proposal, history_proposal):
    _history_model(source_proposal).objects.create(
        version=history_proposal.version,
        hide=True,
        title=history_proposal.title,
        text=history_proposal.text,
        transaction_hash=history_proposal.transaction_hash,
        envelope_xdr=history_proposal.envelope_xdr,
        proposal=history_proposal,
        created_at=history_proposal.last_updated_at,
    )


def _apply_submit_confirmation(
    source_proposal,
    proposal,
    status,
    proposal_status,
    now,
    *,
    create_queue_slot=True,
):
    _create_submit_history(source_proposal, proposal)
    if create_queue_slot:
        _upsert_queue_slot(proposal, proposal.new_start_at, proposal.new_end_at)
    proposal.payment_status = status
    proposal.start_at = proposal.new_start_at
    proposal.end_at = proposal.new_end_at
    proposal.proposal_status = proposal_status
    proposal.last_updated_at = now
    proposal.transaction_hash = proposal.new_transaction_hash
    proposal.envelope_xdr = proposal.new_envelope_xdr
    proposal.action = proposal.NONE
    proposal.new_start_at = None
    proposal.new_end_at = None
    proposal.new_envelope_xdr = None
    proposal.new_transaction_hash = None
    proposal.save()


def _mark_submit_slot_conflict(proposal, status, now):
    proposal.payment_status = status
    proposal.last_updated_at = now
    proposal.save(update_fields=['payment_status', 'last_updated_at'])


def _resolve_submit_proposal_status(source_proposal, proposal, now):
    if proposal.new_start_at and proposal.new_start_at > now:
        return source_proposal.QUEUED
    return source_proposal.VOTING


def _apply_asset_create_transaction(proposal, status):
    proposal_model = type(proposal)
    with transaction.atomic():
        acquire_proposal_transition_lock()
        locked_proposal = proposal_model.objects.select_for_update().get(id=proposal.id)
        if locked_proposal.action == proposal.TO_CREATE:
            locked_proposal.draft = False
            locked_proposal.action = proposal.NONE
            locked_proposal.last_updated_at = timezone.now()
            if status != proposal.FINE:
                locked_proposal.hide = True
            locked_proposal.payment_status = status
            locked_proposal.save()
    proposal.refresh_from_db()


def _upsert_queue_slot(proposal, start_at, end_at):
    from aqua_governance.governance.models import ProposalQueueSlot

    ProposalQueueSlot.objects.update_or_create(
        proposal=proposal,
        defaults={
            'start_at': start_at,
            'end_at': end_at,
        },
    )


def _serialize_queue_conflict(conflict):
    slot = conflict.slot
    return {
        'proposal_id': conflict.proposal.id,
        'proposal_status': conflict.proposal.proposal_status,
        'slot_id': slot.id if slot is not None else None,
        'start_at': slot.start_at if slot is not None else conflict.proposal.start_at,
        'end_at': slot.end_at if slot is not None else conflict.proposal.end_at,
    }


def _log_submit_payment_not_confirmed(proposal, status):
    if status != proposal.HORIZON_ERROR:
        return

    logger.error(
        'Submit payment could not be confirmed yet; queue slot not booked.',
        extra={
            'proposal_id': proposal.id,
            'action': proposal.action,
            'payment_status': status,
            'proposal_status': proposal.proposal_status,
            'transaction_hash': proposal.new_transaction_hash,
            'selected_start_at': proposal.new_start_at,
            'selected_end_at': proposal.new_end_at,
        },
    )


def _log_submit_slot_conflict(proposal, status, conflict):
    logger.error(
        'Confirmed submit payment could not book queue slot because it is already occupied.',
        extra={
            'proposal_id': proposal.id,
            'action': proposal.action,
            'payment_status': status,
            'proposal_status': proposal.proposal_status,
            'transaction_hash': proposal.new_transaction_hash,
            'selected_start_at': proposal.new_start_at,
            'selected_end_at': proposal.new_end_at,
            'conflicting_proposal_id': conflict.proposal.id,
            'conflicting_proposal_status': conflict.proposal.proposal_status,
            'conflicting_slot_id': conflict.slot.id if conflict.slot is not None else None,
            'conflicting_start_at': conflict.slot.start_at if conflict.slot is not None else conflict.proposal.start_at,
            'conflicting_end_at': conflict.slot.end_at if conflict.slot is not None else conflict.proposal.end_at,
        },
    )


def _history_model(proposal):
    return proposal._meta.apps.get_model('governance', 'HistoryProposal')
