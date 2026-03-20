from datetime import timedelta, timezone as dt_timezone
from typing import Any, Optional

from dateutil.parser import parse as date_parse
from django.utils import timezone

from aqua_governance.governance.models import Proposal


UNLOCK_TIMESTAMP_TOLERANCE_SECONDS = 1


def extract_abs_before_values(claimable_balance: dict[str, Any]) -> list[str]:
    abs_before_values = []
    for claimant in claimable_balance.get("claimants", []):
        abs_before = claimant.get("predicate", {}).get("not", {}).get("abs_before")
        if abs_before is not None:
            abs_before_values.append(abs_before)
    return abs_before_values


def get_expected_unlock_timestamp(proposal: Proposal) -> int:
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


def has_valid_unlock_date(claimable_balance: dict[str, Any], expected_unlock_timestamp: int) -> bool:
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
