from django.conf import settings
from django.db import transaction
from django.utils import timezone

from aqua_governance.governance.db_locks import acquire_proposal_transition_lock
from aqua_governance.utils.payments import check_proposal_status


def check_transaction(proposal):
    if proposal.action == proposal.TO_UPDATE:
        _check_update_transaction(proposal)

    elif proposal.action == proposal.TO_SUBMIT:
        _check_submit_transaction(proposal)

    elif proposal.action == proposal.TO_CREATE:
        _check_create_transaction(proposal)


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
        return

    proposal_model = type(proposal)
    with transaction.atomic():
        acquire_proposal_transition_lock()
        locked_proposal = proposal_model.objects.select_for_update().get(id=proposal.id)
        if locked_proposal.action != proposal.TO_SUBMIT:
            proposal.refresh_from_db()
            return

        now = timezone.now()
        new_start_at = locked_proposal.new_start_at
        new_end_at = locked_proposal.new_end_at
        voting_interval_conflict = proposal_model.has_voting_interval_conflict(
            start_at=new_start_at,
            end_at=new_end_at,
            current_proposal_id=locked_proposal.id,
        )

        if new_end_at and new_end_at <= now:
            _apply_submit_window(proposal, locked_proposal, status, proposal.EXPIRED, now)
        elif voting_interval_conflict:
            _clear_conflicting_submit(locked_proposal, status, now)
        else:
            proposal_status = _resolve_submit_proposal_status(proposal, locked_proposal, now)
            _apply_submit_window(proposal, locked_proposal, status, proposal_status, now)

        proposal.refresh_from_db()


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


def _apply_submit_window(source_proposal, proposal, status, proposal_status, now):
    _create_submit_history(source_proposal, proposal)
    proposal.payment_status = status
    proposal.start_at = proposal.new_start_at
    proposal.end_at = proposal.new_end_at
    proposal.proposal_status = proposal_status
    proposal.last_updated_at = now
    proposal.transaction_hash = proposal.new_transaction_hash
    proposal.envelope_xdr = proposal.new_envelope_xdr
    proposal.action = proposal.NONE
    proposal.save()


def _clear_conflicting_submit(proposal, status, now):
    proposal.payment_status = status
    proposal.action = proposal.NONE
    proposal.new_start_at = None
    proposal.new_end_at = None
    proposal.new_envelope_xdr = None
    proposal.new_transaction_hash = None
    proposal.last_updated_at = now
    proposal.save(update_fields=[
        'payment_status',
        'action',
        'new_start_at',
        'new_end_at',
        'new_envelope_xdr',
        'new_transaction_hash',
        'last_updated_at',
    ])


def _resolve_submit_proposal_status(source_proposal, proposal, now):
    if proposal.new_start_at and proposal.new_start_at > now:
        return source_proposal.DISCUSSION
    if type(source_proposal).has_active_voting_proposal_conflict(current_proposal_id=proposal.id):
        return source_proposal.DISCUSSION
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


def _history_model(proposal):
    return proposal._meta.apps.get_model('governance', 'HistoryProposal')
