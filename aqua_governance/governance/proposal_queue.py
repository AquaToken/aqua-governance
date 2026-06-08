from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

if TYPE_CHECKING:
    from aqua_governance.governance.models import Proposal, ProposalQueueSlot


UTC = datetime_timezone.utc
QUEUE_SLOT_DURATION = timedelta(days=7)
QUEUE_SLOT_END_INCLUSIVE_OFFSET = timedelta(seconds=1)


@dataclass(frozen=True)
class QueueSlotConflict:
    proposal: Proposal
    slot: ProposalQueueSlot | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or timezone.is_naive(value):
        return None
    return value.astimezone(UTC)


def get_queue_week_start(now: datetime | None = None) -> datetime:
    current = _as_utc(now or timezone.now())
    if current is None:
        raise ValueError('now must be timezone-aware.')
    return current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=current.weekday())


def is_utc_monday_start(start_at: datetime | None) -> bool:
    start_at_utc = _as_utc(start_at)
    if start_at_utc is None:
        return False

    return (
        start_at_utc.weekday() == 0
        and start_at_utc.hour == 0
        and start_at_utc.minute == 0
        and start_at_utc.second == 0
        and start_at_utc.microsecond == 0
    )


def has_exact_weekly_range(start_at: datetime | None, end_at: datetime | None) -> bool:
    start_at_utc = _as_utc(start_at)
    end_at_utc = _as_utc(end_at)
    if start_at_utc is None or end_at_utc is None:
        return False

    return (
        is_utc_monday_start(start_at_utc)
        and end_at_utc == start_at_utc + QUEUE_SLOT_DURATION - QUEUE_SLOT_END_INCLUSIVE_OFFSET
    )


def get_max_booking_datetime(
    now: datetime | None = None,
    booking_horizon_weeks: int | None = None,
) -> datetime:
    horizon_weeks = (
        settings.PROPOSAL_QUEUE_BOOKING_HORIZON_WEEKS
        if booking_horizon_weeks is None
        else booking_horizon_weeks
    )
    if horizon_weeks < 1:
        raise ValueError('booking_horizon_weeks must be greater than zero.')

    return get_queue_week_start(now=now) + timedelta(weeks=horizon_weeks) - QUEUE_SLOT_END_INCLUSIVE_OFFSET


def is_within_booking_horizon(
    start_at: datetime | None,
    end_at: datetime | None,
    *,
    now: datetime | None = None,
    booking_horizon_weeks: int | None = None,
) -> bool:
    start_at_utc = _as_utc(start_at)
    end_at_utc = _as_utc(end_at)
    if start_at_utc is None or end_at_utc is None or end_at_utc <= start_at_utc:
        return False

    current_week_start = get_queue_week_start(now=now)
    max_booking_datetime = get_max_booking_datetime(
        now=now,
        booking_horizon_weeks=booking_horizon_weeks,
    )
    return (
        start_at_utc >= current_week_start
        and end_at_utc <= max_booking_datetime
    )


def validate_weekly_queue_slot(
    start_at: datetime | None,
    end_at: datetime | None,
    *,
    now: datetime | None = None,
    booking_horizon_weeks: int | None = None,
) -> None:
    errors = {}

    if start_at is None:
        errors['start_at'] = 'start_at is required.'
    elif timezone.is_naive(start_at):
        errors['start_at'] = 'start_at must be timezone-aware.'
    elif not is_utc_monday_start(start_at):
        errors['start_at'] = 'start_at must be a UTC Monday 00:00:00.'

    if end_at is None:
        errors['end_at'] = 'end_at is required.'
    elif timezone.is_naive(end_at):
        errors['end_at'] = 'end_at must be timezone-aware.'

    if errors:
        raise ValidationError(errors)

    assert start_at is not None
    assert end_at is not None
    start_at_utc = start_at.astimezone(UTC)
    end_at_utc = end_at.astimezone(UTC)

    if end_at_utc <= start_at_utc:
        errors['end_at'] = 'end_at must be greater than start_at.'
    elif not has_exact_weekly_range(start_at_utc, end_at_utc):
        errors['end_at'] = 'end_at must be the following Sunday 23:59:59 UTC for a weekly queue slot.'

    if not errors and not is_within_booking_horizon(
        start_at_utc,
        end_at_utc,
        now=now,
        booking_horizon_weeks=booking_horizon_weeks,
    ):
        errors['end_at'] = 'Selected queue slot falls outside the booking horizon.'

    if errors:
        raise ValidationError(errors)


def is_queue_slot_available(
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_proposal_id: int | None = None,
) -> bool:
    return find_queue_slot_conflict(
        start_at,
        end_at,
        exclude_proposal_id=exclude_proposal_id,
    ) is None


def find_queue_slot_conflict(
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_proposal_id: int | None = None,
) -> QueueSlotConflict | None:
    from aqua_governance.governance.models import Proposal, ProposalQueueSlot

    queryset = ProposalQueueSlot.objects.filter(
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).select_related('proposal')
    if exclude_proposal_id is not None:
        queryset = queryset.exclude(proposal_id=exclude_proposal_id)

    slot = queryset.order_by('start_at', 'id').first()
    if slot is not None:
        return QueueSlotConflict(proposal=slot.proposal, slot=slot)

    legacy_queryset = Proposal.objects.filter(
        hide=False,
        draft=False,
        action=Proposal.NONE,
        # Transitional compatibility for rows that still mirror an occupied
        # queue/voting window on Proposal.start_at/end_at but do not yet have a
        # ProposalQueueSlot row. Slot-less DISCUSSION rows are intentionally not
        # blockers anymore because draft/create-time reservation is retired.
        proposal_status__in=(Proposal.QUEUED, Proposal.VOTING),
        start_at__isnull=False,
        end_at__isnull=False,
        start_at__lt=end_at,
        end_at__gt=start_at,
        queue_slot__isnull=True,
    )
    if exclude_proposal_id is not None:
        legacy_queryset = legacy_queryset.exclude(id=exclude_proposal_id)

    legacy_proposal = legacy_queryset.order_by('start_at', 'id').first()
    if legacy_proposal is not None:
        return QueueSlotConflict(proposal=legacy_proposal)

    return None
