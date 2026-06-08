from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from aqua_governance.governance.models import Proposal, ProposalQueueSlot
from aqua_governance.governance.proposal_queue import get_queue_week_start
from aqua_governance.governance.serializers_v2 import SubmitSerializer
from aqua_governance.governance.tests._factories import make_asset_proposal_raw, patch_ice_circulating_supply


class SubmitBookingFlowTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.ice_supply_patcher = patch_ice_circulating_supply()
        self.ice_supply_patcher.start()
        self.addCleanup(self.ice_supply_patcher.stop)

    def _proposal(self, **overrides):
        defaults = {
            'proposal_type': Proposal.PROPOSAL_TYPE_GENERAL,
            'proposal_status': Proposal.DISCUSSION,
            'draft': False,
            'action': Proposal.NONE,
        }
        defaults.update(overrides)
        return make_asset_proposal_raw(**defaults)

    def _week_slot(self, *, weeks_ahead=1):
        start_at = get_queue_week_start(timezone.now()) + timedelta(weeks=weeks_ahead)
        end_at = start_at + timedelta(days=7, seconds=-1)
        return start_at, end_at

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_serializer_stages_public_start_and_end_without_booking_slot(self, _mock_check_xdr):
        proposal = self._proposal()
        start_at, end_at = self._week_slot(weeks_ahead=1)

        serializer = SubmitSerializer(
            proposal,
            data={
                'start_at': start_at,
                'end_at': end_at,
                'new_envelope_xdr': 'submit-xdr',
                'new_transaction_hash': 'a' * 64,
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        proposal.refresh_from_db()
        self.assertEqual(proposal.action, Proposal.TO_SUBMIT)
        self.assertEqual(proposal.new_start_at, start_at)
        self.assertEqual(proposal.new_end_at, end_at)
        self.assertIsNone(proposal.start_at)
        self.assertIsNone(proposal.end_at)
        self.assertFalse(ProposalQueueSlot.objects.filter(proposal=proposal).exists())

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_serializer_rejects_occupied_slot(self, _mock_check_xdr):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        ProposalQueueSlot.objects.create(
            proposal=self._proposal(title='Occupied slot'),
            start_at=start_at,
            end_at=end_at,
        )
        proposal = self._proposal(title='Target proposal', transaction_hash='b' * 64)

        serializer = SubmitSerializer(
            proposal,
            data={
                'start_at': start_at,
                'end_at': end_at,
                'new_envelope_xdr': 'submit-xdr',
                'new_transaction_hash': 'c' * 64,
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('start_at', serializer.errors)
        self.assertIn('end_at', serializer.errors)

    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.FINE)
    def test_confirmed_submit_books_future_slot_and_sets_queued(self, _mock_check_status):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        proposal = self._proposal(
            action=Proposal.TO_SUBMIT,
            payment_status=Proposal.HORIZON_ERROR,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='d' * 64,
        )

        result = proposal.check_transaction()

        proposal.refresh_from_db()
        self.assertEqual(result['outcome'], 'booked')
        self.assertEqual(proposal.proposal_status, Proposal.QUEUED)
        self.assertEqual(proposal.action, Proposal.NONE)
        self.assertEqual(proposal.payment_status, Proposal.FINE)
        self.assertEqual(proposal.start_at, start_at)
        self.assertEqual(proposal.end_at, end_at)
        self.assertIsNone(proposal.new_start_at)
        self.assertIsNone(proposal.new_end_at)
        self.assertTrue(ProposalQueueSlot.objects.filter(proposal=proposal, start_at=start_at, end_at=end_at).exists())

    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.FINE)
    def test_confirmed_submit_starts_voting_when_slot_is_already_active(self, _mock_check_status):
        start_at, end_at = self._week_slot(weeks_ahead=0)
        proposal = self._proposal(
            action=Proposal.TO_SUBMIT,
            payment_status=Proposal.HORIZON_ERROR,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='e' * 64,
        )

        result = proposal.check_transaction()

        proposal.refresh_from_db()
        self.assertEqual(result['outcome'], 'booked')
        self.assertEqual(proposal.proposal_status, Proposal.VOTING)
        self.assertTrue(ProposalQueueSlot.objects.filter(proposal=proposal, start_at=start_at, end_at=end_at).exists())

    @patch('aqua_governance.governance.proposal_transactions.logger.error')
    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.HORIZON_ERROR)
    def test_horizon_error_keeps_to_submit_without_booking_slot(self, _mock_check_status, mock_logger):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        proposal = self._proposal(
            action=Proposal.TO_SUBMIT,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='f' * 64,
        )

        result = proposal.check_transaction()

        proposal.refresh_from_db()
        self.assertEqual(result['outcome'], 'payment_not_confirmed')
        self.assertEqual(proposal.action, Proposal.TO_SUBMIT)
        self.assertEqual(proposal.payment_status, Proposal.HORIZON_ERROR)
        self.assertFalse(ProposalQueueSlot.objects.filter(proposal=proposal).exists())
        mock_logger.assert_called_once()

    @patch('aqua_governance.governance.proposal_transactions.logger.error')
    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.FINE)
    def test_paid_slot_conflict_keeps_submit_state_and_does_not_book_slot(self, _mock_check_status, mock_logger):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        blocking_proposal = self._proposal(title='Blocking proposal')
        ProposalQueueSlot.objects.create(
            proposal=blocking_proposal,
            start_at=start_at,
            end_at=end_at,
        )
        proposal = self._proposal(
            title='Target proposal',
            transaction_hash='1' * 64,
            action=Proposal.TO_SUBMIT,
            payment_status=Proposal.HORIZON_ERROR,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='2' * 64,
        )

        result = proposal.check_transaction()

        proposal.refresh_from_db()
        self.assertEqual(result['outcome'], 'slot_conflict')
        self.assertEqual(result['conflict']['proposal_id'], blocking_proposal.id)
        self.assertEqual(proposal.action, Proposal.TO_SUBMIT)
        self.assertEqual(proposal.payment_status, Proposal.FINE)
        self.assertEqual(proposal.proposal_status, Proposal.DISCUSSION)
        self.assertIsNone(proposal.start_at)
        self.assertIsNone(proposal.end_at)
        self.assertEqual(proposal.new_start_at, start_at)
        self.assertEqual(proposal.new_end_at, end_at)
        self.assertFalse(ProposalQueueSlot.objects.filter(proposal=proposal).exists())
        mock_logger.assert_called_once()

    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', side_effect=[Proposal.HORIZON_ERROR, Proposal.FINE])
    def test_retry_later_books_slot_when_confirmation_eventually_succeeds(self, _mock_check_status):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        proposal = self._proposal(
            action=Proposal.TO_SUBMIT,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='3' * 64,
        )

        first_result = proposal.check_transaction()
        second_result = proposal.check_transaction()

        proposal.refresh_from_db()
        self.assertEqual(first_result['outcome'], 'payment_not_confirmed')
        self.assertEqual(second_result['outcome'], 'booked')
        self.assertEqual(proposal.proposal_status, Proposal.QUEUED)
        self.assertTrue(ProposalQueueSlot.objects.filter(proposal=proposal, start_at=start_at, end_at=end_at).exists())

    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.FINE)
    def test_check_payment_endpoint_returns_conflict_response(self, _mock_check_status):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        blocking_proposal = self._proposal(title='Blocking proposal')
        ProposalQueueSlot.objects.create(
            proposal=blocking_proposal,
            start_at=start_at,
            end_at=end_at,
        )
        proposal = self._proposal(
            title='Pending proposal',
            transaction_hash='4' * 64,
            action=Proposal.TO_SUBMIT,
            new_start_at=start_at,
            new_end_at=end_at,
            new_envelope_xdr='submit-xdr',
            new_transaction_hash='5' * 64,
        )

        response = self.client.post(f'/api/proposal/{proposal.id}/check_payment/', {}, format='json')

        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body['code'], 'proposal_queue_slot_conflict')
        self.assertEqual(body['conflict']['proposal_id'], blocking_proposal.id)

    def test_queued_proposal_is_not_publicly_editable(self):
        start_at, end_at = self._week_slot(weeks_ahead=1)
        proposal = self._proposal(
            proposal_status=Proposal.QUEUED,
            start_at=start_at,
            end_at=end_at,
        )
        ProposalQueueSlot.objects.create(proposal=proposal, start_at=start_at, end_at=end_at)

        response = self.client.put(f'/api/proposal/{proposal.id}/', {}, format='json')

        self.assertEqual(response.status_code, 404)
