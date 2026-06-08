from datetime import timedelta, timezone as datetime_timezone

from django.db import migrations
from django.utils import timezone


ASSET_PROPOSAL_TYPES = ('ADD_ASSET', 'REMOVE_ASSET')
ELIGIBLE_STATUSES = ('DISCUSSION', 'VOTING')
UTC = datetime_timezone.utc
QUEUE_SLOT_DURATION = timedelta(days=7)
QUEUE_SLOT_END_INCLUSIVE_OFFSET = timedelta(seconds=1)


def _as_utc(value, *, proposal_id, field_name):
    if value is None or timezone.is_naive(value):
        raise ValueError(
            f'Proposal {proposal_id} has invalid {field_name}: expected a timezone-aware datetime.'
        )
    return value.astimezone(UTC)


def _is_utc_monday_start(value):
    return (
        value.weekday() == 0
        and value.hour == 0
        and value.minute == 0
        and value.second == 0
        and value.microsecond == 0
    )


def _has_exact_weekly_range(start_at, end_at):
    return (
        _is_utc_monday_start(start_at)
        and end_at == start_at + QUEUE_SLOT_DURATION - QUEUE_SLOT_END_INCLUSIVE_OFFSET
    )


def _validate_candidate_window(proposal, now):
    start_at = _as_utc(proposal.start_at, proposal_id=proposal.id, field_name='start_at')
    end_at = _as_utc(proposal.end_at, proposal_id=proposal.id, field_name='end_at')

    if end_at <= start_at:
        raise ValueError(f'Proposal {proposal.id} has invalid queue slot range: end_at must be after start_at.')

    if not _has_exact_weekly_range(start_at, end_at):
        raise ValueError(
            f'Proposal {proposal.id} has invalid queue slot range: '
            'expected Monday 00:00:00 UTC -> Sunday 23:59:59 UTC.'
        )

    if proposal.proposal_status == 'DISCUSSION' and start_at <= now:
        raise ValueError(
            f'Proposal {proposal.id} is DISCUSSION but its scheduled slot already started.'
        )

    if proposal.proposal_status == 'VOTING' and not (start_at <= now <= end_at):
        raise ValueError(
            f'Proposal {proposal.id} is VOTING but is not active at migration time.'
        )

    return start_at, end_at


def backfill_proposal_queue_slots(apps, schema_editor):
    Proposal = apps.get_model('governance', 'Proposal')
    ProposalQueueSlot = apps.get_model('governance', 'ProposalQueueSlot')

    now = timezone.now().astimezone(UTC)
    candidates = list(
        Proposal.objects.filter(
            proposal_type__in=ASSET_PROPOSAL_TYPES,
            hide=False,
            draft=False,
            payment_status='FINE',
            proposal_status__in=ELIGIBLE_STATUSES,
            start_at__isnull=False,
            end_at__isnull=False,
            end_at__gte=now,
        ).order_by('start_at', 'id')
    )

    validated_candidates = []
    previous_proposal = None
    previous_start_at = None
    previous_end_at = None

    for proposal in candidates:
        existing_slot = ProposalQueueSlot.objects.filter(proposal_id=proposal.id).first()
        if existing_slot is not None:
            raise ValueError(f'Proposal {proposal.id} already has a queue slot row.')

        start_at, end_at = _validate_candidate_window(proposal, now)

        conflicting_slot = ProposalQueueSlot.objects.filter(
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).order_by('start_at', 'id').first()
        if conflicting_slot is not None:
            raise ValueError(
                f'Proposal {proposal.id} conflicts with existing queue slot '
                f'for proposal {conflicting_slot.proposal_id}.'
            )

        if previous_proposal is not None and start_at <= previous_end_at:
            assert previous_start_at is not None
            assert previous_end_at is not None
            raise ValueError(
                'Eligible asset proposal queue slots overlap or duplicate: '
                f'proposal {previous_proposal.id} ({previous_start_at.isoformat()} -> {previous_end_at.isoformat()}) '
                f'conflicts with proposal {proposal.id} ({start_at.isoformat()} -> {end_at.isoformat()}).'
            )

        validated_candidates.append((proposal, start_at, end_at))
        previous_proposal = proposal
        previous_start_at = start_at
        previous_end_at = end_at

    ProposalQueueSlot.objects.bulk_create([
        ProposalQueueSlot(
            proposal_id=proposal.id,
            start_at=start_at,
            end_at=end_at,
        )
        for proposal, start_at, end_at in validated_candidates
    ])

    queued_ids = [
        proposal.id
        for proposal, _start_at, _end_at in validated_candidates
        if proposal.proposal_status == 'DISCUSSION'
    ]
    if queued_ids:
        Proposal.objects.filter(id__in=queued_ids).update(proposal_status='QUEUED')


class Migration(migrations.Migration):

    dependencies = [
        ('governance', '0029_proposal_queue_slot'),
    ]

    operations = [
        migrations.RunPython(
            backfill_proposal_queue_slots,
            # Exact reverse attribution is unsafe without persistent metadata that marks
            # which ProposalQueueSlot rows came from this one-time backfill.
            reverse_code=migrations.RunPython.noop,
        ),
    ]
