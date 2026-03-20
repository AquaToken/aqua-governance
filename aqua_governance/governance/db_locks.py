from django.conf import settings
from django.db import connection


def acquire_asset_proposal_transition_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [settings.ASSET_PROPOSAL_TRANSITION_ADVISORY_LOCK_ID],
        )
