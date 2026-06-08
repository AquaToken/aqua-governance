from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from aqua_governance.governance.admin import ProposalQueueSlotAdmin
from aqua_governance.governance.models import Proposal, ProposalQueueSlot
from aqua_governance.governance.proposal_queue import (
    get_max_booking_datetime,
    has_exact_weekly_range,
    is_queue_slot_available,
    is_utc_monday_start,
    validate_weekly_queue_slot,
)
from aqua_governance.governance.tests._factories import make_asset_proposal_raw


UTC = datetime_timezone.utc


class ProposalQueueHelperTests(SimpleTestCase):
    def test_is_utc_monday_start_accepts_exact_utc_boundary(self):
        self.assertTrue(is_utc_monday_start(datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC)))

    def test_is_utc_monday_start_rejects_non_utc_aligned_datetime(self):
        plus_two = datetime_timezone(timedelta(hours=2))
        self.assertFalse(is_utc_monday_start(datetime(2026, 6, 8, 0, 0, 0, tzinfo=plus_two)))

    def test_has_exact_weekly_range_requires_last_sunday_second_boundary(self):
        start_at = datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC)
        self.assertTrue(has_exact_weekly_range(start_at, start_at + timedelta(days=7, seconds=-1)))
        self.assertFalse(has_exact_weekly_range(start_at, start_at + timedelta(days=7)))

    def test_get_max_booking_datetime_uses_last_sunday_second(self):
        now = datetime(2026, 6, 10, 15, 30, 0, tzinfo=UTC)

        self.assertEqual(
            get_max_booking_datetime(now=now, booking_horizon_weeks=6),
            datetime(2026, 7, 19, 23, 59, 59, tzinfo=UTC),
        )

    @override_settings(PROPOSAL_QUEUE_BOOKING_HORIZON_WEEKS=2)
    def test_validate_weekly_queue_slot_accepts_last_allowed_week(self):
        now = datetime(2026, 6, 10, 15, 30, 0, tzinfo=UTC)
        start_at = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 6, 21, 23, 59, 59, tzinfo=UTC)

        validate_weekly_queue_slot(start_at, end_at, now=now)

    def test_validate_weekly_queue_slot_rejects_next_monday_midnight_end(self):
        start_at = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 6, 22, 0, 0, 0, tzinfo=UTC)

        with self.assertRaises(ValidationError) as error:
            validate_weekly_queue_slot(start_at, end_at, now=datetime(2026, 6, 10, 15, 30, 0, tzinfo=UTC))

        self.assertEqual(
            error.exception.message_dict,
            {'end_at': ['end_at must be the following Sunday 23:59:59 UTC for a weekly queue slot.']},
        )

    @override_settings(PROPOSAL_QUEUE_BOOKING_HORIZON_WEEKS=2)
    def test_validate_weekly_queue_slot_rejects_outside_booking_horizon(self):
        now = datetime(2026, 6, 10, 15, 30, 0, tzinfo=UTC)
        start_at = datetime(2026, 6, 22, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 6, 28, 23, 59, 59, tzinfo=UTC)

        with self.assertRaises(ValidationError) as error:
            validate_weekly_queue_slot(start_at, end_at, now=now)

        self.assertEqual(
            error.exception.message_dict,
            {'end_at': ['Selected queue slot falls outside the booking horizon.']},
        )

    def test_validate_weekly_queue_slot_rejects_non_monday_start(self):
        start_at = datetime(2026, 6, 9, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 6, 15, 23, 59, 59, tzinfo=UTC)

        with self.assertRaises(ValidationError) as error:
            validate_weekly_queue_slot(start_at, end_at, now=datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC))

        self.assertEqual(
            error.exception.message_dict,
            {'start_at': ['start_at must be a UTC Monday 00:00:00.']},
        )


class ProposalQueueSlotModelTests(TestCase):
    def _make_proposal(self, **overrides):
        return make_asset_proposal_raw(
            proposal_type=overrides.pop('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL),
            **overrides,
        )

    def test_slot_start_at_must_be_unique(self):
        start_at = datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC)
        ProposalQueueSlot.objects.create(
            proposal=self._make_proposal(title='One'),
            start_at=start_at,
            end_at=end_at,
        )

        with self.assertRaises(IntegrityError):
            ProposalQueueSlot.objects.create(
                proposal=self._make_proposal(title='Two'),
                start_at=start_at,
                end_at=end_at,
            )

    def test_proposal_can_only_have_one_slot(self):
        proposal = self._make_proposal(title='One')
        ProposalQueueSlot.objects.create(
            proposal=proposal,
            start_at=datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
        )

        with self.assertRaises(IntegrityError):
            ProposalQueueSlot.objects.create(
                proposal=proposal,
                start_at=datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC),
                end_at=datetime(2026, 6, 21, 23, 59, 59, tzinfo=UTC),
            )

    def test_queue_slot_availability_checks_overlaps(self):
        proposal = self._make_proposal(title='Occupied')
        ProposalQueueSlot.objects.create(
            proposal=proposal,
            start_at=datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
        )

        self.assertFalse(
            is_queue_slot_available(
                datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
            )
        )
        self.assertFalse(
            is_queue_slot_available(
                datetime(2026, 6, 14, 23, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 21, 23, 59, 59, tzinfo=UTC),
            )
        )
        self.assertTrue(
            is_queue_slot_available(
                datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 21, 23, 59, 59, tzinfo=UTC),
            )
        )
        self.assertTrue(
            is_queue_slot_available(
                datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
                exclude_proposal_id=proposal.id,
            )
        )

    def test_queue_slot_availability_checks_legacy_scheduled_proposals_without_queue_rows(self):
        self._make_proposal(
            title='Legacy scheduled proposal',
            start_at=datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
            proposal_status=Proposal.DISCUSSION,
            action=Proposal.NONE,
        )

        self.assertFalse(
            is_queue_slot_available(
                datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC),
                datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC),
            )
        )


EXPECTED_SLOT_KEYS = {
    'id',
    'proposal',
    'start_at',
    'end_at',
    'created_at',
    'updated_at',
}

EXPECTED_PROPOSAL_KEYS = {
    'id',
    'proposed_by',
    'title',
    'proposal_status',
    'proposal_type',
    'vote_for_result',
    'vote_against_result',
    'vote_abstain_result',
    'percent_for_quorum',
    'aqua_circulating_supply',
    'ice_circulating_supply',
    'created_at',
    'last_updated_at',
}


class ProposalQueueApiTests(TestCase):
    def _make_proposal(self, **overrides):
        return make_asset_proposal_raw(
            proposal_type=overrides.pop('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL),
            **overrides,
        )

    def _make_slot(self, proposal=None, start_at=None, end_at=None):
        if proposal is None:
            proposal = self._make_proposal()
        if start_at is None:
            start_at = datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC)
        if end_at is None:
            end_at = datetime(2026, 6, 14, 23, 59, 59, tzinfo=UTC)
        return ProposalQueueSlot.objects.create(
            proposal=proposal,
            start_at=start_at,
            end_at=end_at,
        )

    def setUp(self):
        Proposal.objects.all().delete()
        ProposalQueueSlot.objects.all().delete()
        self.client = APIClient()

    def test_response_shape(self):
        self._make_slot()
        response = self.client.get('/api/proposal-queue/')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(len(body['results']), 1)

        slot = body['results'][0]
        self.assertEqual(set(slot.keys()), EXPECTED_SLOT_KEYS)
        self.assertEqual(set(slot['proposal'].keys()), EXPECTED_PROPOSAL_KEYS)

    def test_response_includes_max_booking_datetime_and_horizon(self):
        self._make_slot()
        response = self.client.get('/api/proposal-queue/')

        body = response.json()
        self.assertIn('max_booking_datetime', body)
        self.assertIsNotNone(body['max_booking_datetime'])
        self.assertIn('booking_horizon_weeks', body)
        self.assertGreater(body['booking_horizon_weeks'], 0)

    def test_returns_only_current_and_future_slots(self):
        # past slot
        past_start = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
        past_end = datetime(2026, 1, 11, 23, 59, 59, tzinfo=UTC)
        self._make_slot(
            proposal=self._make_proposal(title='Past'),
            start_at=past_start,
            end_at=past_end,
        )
        # future slot
        future_start = datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)
        future_end = datetime(2026, 7, 12, 23, 59, 59, tzinfo=UTC)
        future_slot = self._make_slot(
            proposal=self._make_proposal(title='Future'),
            start_at=future_start,
            end_at=future_end,
        )

        response = self.client.get('/api/proposal-queue/')

        body = response.json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['results'][0]['id'], future_slot.id)

    def test_returns_multiple_occupied_slots(self):
        slot1 = self._make_slot(
            proposal=self._make_proposal(title='One'),
            start_at=datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 12, 23, 59, 59, tzinfo=UTC),
        )
        slot2 = self._make_slot(
            proposal=self._make_proposal(title='Two'),
            start_at=datetime(2026, 7, 13, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 19, 23, 59, 59, tzinfo=UTC),
        )

        response = self.client.get('/api/proposal-queue/')

        body = response.json()
        self.assertEqual(body['count'], 2)
        result_ids = {r['id'] for r in body['results']}
        self.assertEqual(result_ids, {slot1.id, slot2.id})

    def test_only_get_method_allowed(self):
        # POST to list endpoint not allowed (no CreateModelMixin)
        response = self.client.post('/api/proposal-queue/', {}, format='json')
        self.assertEqual(response.status_code, 405)

        # PUT/DELETE on detail rout not available (no detail actions registered)
        response = self.client.put('/api/proposal-queue/1/', {}, format='json')
        self.assertEqual(response.status_code, 404)

        response = self.client.delete('/api/proposal-queue/1/')
        self.assertEqual(response.status_code, 404)


class ProposalQueueSlotAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = ProposalQueueSlotAdmin(ProposalQueueSlot, self.site)

    def test_admin_is_registered(self):
        """Smoke test: admin class can be instantiated."""
        self.assertIsNotNone(self.admin)

    def test_admin_has_no_add_permission(self):
        self.assertFalse(self.admin.has_add_permission(None))

    def test_admin_has_no_change_permission(self):
        self.assertFalse(self.admin.has_change_permission(None))

    def test_admin_has_no_delete_permission(self):
        self.assertFalse(self.admin.has_delete_permission(None))

    def test_admin_list_display_includes_key_fields(self):
        list_display = self.admin.get_list_display(None)
        self.assertIn('id', list_display)
        self.assertIn('proposal_id', list_display)
        self.assertIn('start_at', list_display)
        self.assertIn('end_at', list_display)
