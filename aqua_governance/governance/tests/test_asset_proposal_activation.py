import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from django_quill.quill import Quill
from rest_framework.test import APIClient

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.serializers_v2 import ProposalCreateSerializer
from aqua_governance.governance.serializers_v2 import SubmitSerializer
from aqua_governance.governance.tasks import task_check_expired_proposals, task_check_pending_proposal_payments
from aqua_governance.governance.tests._factories import DEFAULT_PROPOSED_BY, patch_ice_circulating_supply
from aqua_governance.taskapp import app as celery_app


class AssetProposalActivationTests(TestCase):
    def setUp(self):
        super().setUp()
        self.ice_supply_patcher = patch_ice_circulating_supply()
        self.ice_supply_patcher.start()
        self.addCleanup(self.ice_supply_patcher.stop)

    def _create_proposal(self, **overrides):
        from aqua_governance.governance.tests._factories import make_asset_proposal_raw
        kwargs = {
            'proposal_type': overrides.pop('proposal_type', Proposal.PROPOSAL_TYPE_ADD_ASSET),
            'asset_code': overrides.pop('asset_code', 'AQUA'),
            'asset_issuer': overrides.pop(
                'asset_issuer', 'GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA',
            ),
            'transaction_hash': overrides.pop('transaction_hash', None),
            'draft': overrides.pop('draft', False),
            'action': overrides.pop('action', Proposal.NONE),
            'proposal_status': overrides.pop('proposal_status', Proposal.DISCUSSION),
        }
        kwargs.update(overrides)
        return make_asset_proposal_raw(**kwargs)

    def _general_create_payload(self, **overrides):
        data = {
            'proposed_by': DEFAULT_PROPOSED_BY,
            'title': 'Test proposal',
            'text': '<p>Test</p>',
            'transaction_hash': overrides.pop('transaction_hash', 'a' * 64),
            'envelope_xdr': overrides.pop('envelope_xdr', 'AAAA'),
            'discord_username': 'tester',
        }
        data.update(overrides)
        return data

    def test_create_rejects_matching_pending_proposal(self):
        pending = Proposal.objects.create(
            proposed_by=DEFAULT_PROPOSED_BY,
            title='Test proposal',
            text=Quill(json.dumps({'delta': '', 'html': '<p>Test</p>'})),
            transaction_hash='b' * 64,
            draft=True,
            action=Proposal.TO_CREATE,
            payment_status=Proposal.FINE,
            hide=False,
        )

        serializer = ProposalCreateSerializer(data=self._general_create_payload())

        self.assertFalse(serializer.is_valid())
        self.assertEqual(int(serializer.errors['proposal_id'][0]), pending.id)
        self.assertIn('Please wait a few minutes', serializer.errors['non_field_errors'][0])

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.HORIZON_ERROR)
    def test_create_keeps_horizon_error_proposal_visible_for_retry(self, _mock_check_xdr):
        serializer = ProposalCreateSerializer(data=self._general_create_payload())

        self.assertTrue(serializer.is_valid(), serializer.errors)
        proposal = serializer.save()

        self.assertTrue(proposal.draft)
        self.assertFalse(proposal.hide)
        self.assertEqual(proposal.action, Proposal.TO_CREATE)
        self.assertEqual(proposal.payment_status, Proposal.HORIZON_ERROR)

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_create_uses_create_or_update_payment_cost(self, mock_check_xdr):
        serializer = ProposalCreateSerializer(data=self._general_create_payload())

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        self.assertEqual(mock_check_xdr.call_args.args[1], settings.PROPOSAL_CREATE_OR_UPDATE_COST)

    @patch('aqua_governance.governance.models.Proposal.check_transaction')
    def test_pending_payment_task_retries_visible_pending_creates(self, mock_check_transaction):
        pending = Proposal.objects.create(
            proposed_by=DEFAULT_PROPOSED_BY,
            title='Test proposal',
            text=Quill(json.dumps({'delta': '', 'html': '<p>Test</p>'})),
            transaction_hash='b' * 64,
            draft=True,
            action=Proposal.TO_CREATE,
            payment_status=Proposal.FINE,
            hide=False,
        )
        Proposal.objects.create(
            proposed_by=DEFAULT_PROPOSED_BY,
            title='Hidden proposal',
            text=Quill(json.dumps({'delta': '', 'html': '<p>Hidden</p>'})),
            transaction_hash='c' * 64,
            draft=True,
            action=Proposal.TO_CREATE,
            payment_status=Proposal.INVALID_PAYMENT,
            hide=True,
        )

        task_check_pending_proposal_payments()

        mock_check_transaction.assert_called_once()
        self.assertEqual(
            Proposal.objects.filter(id=pending.id, hide=False, action=Proposal.TO_CREATE).count(),
            1,
        )

    def test_pending_payment_task_is_scheduled(self):
        celery_app.finalize(auto=True)

        self.assertIn(
            'aqua_governance.governance.tasks.task_check_pending_proposal_payments',
            celery_app.conf.beat_schedule,
        )

    @patch('aqua_governance.governance.proposal_transactions.check_proposal_status', return_value=Proposal.FINE)
    def test_queued_asset_proposal_refreshes_last_updated_at_on_activation(self, _mock_check_status):
        blocker = self._create_proposal()
        queued = self._create_proposal(
            transaction_hash='b' * 64,
            draft=True,
            action=Proposal.TO_CREATE,
        )

        stale_time = timezone.now() - timedelta(days=31)
        Proposal.objects.filter(id=queued.id).update(last_updated_at=stale_time)
        queued.refresh_from_db()

        blocker.proposal_status = Proposal.EXPIRED
        blocker.save(update_fields=['proposal_status'])

        queued.check_transaction()
        queued.refresh_from_db()

        self.assertFalse(queued.draft)
        self.assertEqual(queued.action, Proposal.NONE)
        self.assertEqual(queued.proposal_status, Proposal.DISCUSSION)
        self.assertGreater(queued.last_updated_at, stale_time)

        task_check_expired_proposals()
        queued.refresh_from_db()

        self.assertEqual(queued.proposal_status, Proposal.DISCUSSION)

    def test_asset_proposal_list_includes_asset_fields(self):
        self._create_proposal(
            proposal_status=Proposal.VOTING,
            asset_code=None,
            asset_issuer=None,
            asset_contract_address='CBL6KD2LFMLAUKFFWNNXWOXFN73GAXLEA4WMJRLQ5L76DMYTM3KWQVJN',
        )

        response = APIClient().get('/api/proposal/', {
            'proposal_type': 'asset',
            'status': 'voting',
            'limit': 1,
            'page': 1,
            'ordering': '-created_at',
        })

        self.assertEqual(response.status_code, 200)
        proposal = response.data['results'][0]
        self.assertIsNone(proposal['asset_code'])
        self.assertIsNone(proposal['asset_issuer'])
        self.assertEqual(
            proposal['asset_contract_address'],
            'CBL6KD2LFMLAUKFFWNNXWOXFN73GAXLEA4WMJRLQ5L76DMYTM3KWQVJN',
        )
        self.assertEqual(proposal['asset_token_description'], 'desc')

    def test_asset_proposal_submit_allows_when_no_asset_proposal_is_in_voting(self):
        now = timezone.now()
        queued = self._create_proposal()

        serializer = SubmitSerializer(queued, data={
            'new_start_at': now,
            'new_end_at': now + timedelta(days=10),
            'new_envelope_xdr': 'AAAA',
            'new_transaction_hash': 'a' * 64,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_uses_submit_payment_cost(self, mock_check_xdr):
        now = timezone.now()
        queued = self._create_proposal()

        serializer = SubmitSerializer(queued, data={
            'new_start_at': now + timedelta(days=1),
            'new_end_at': now + timedelta(days=8),
            'new_envelope_xdr': 'AAAA',
            'new_transaction_hash': 'd' * 64,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        self.assertEqual(mock_check_xdr.call_args.args[1], settings.PROPOSAL_SUBMIT_COST)

    def test_asset_proposal_submit_rejects_when_asset_interval_overlaps_voting(self):
        now = timezone.now()
        self._create_proposal(
            proposal_status=Proposal.VOTING,
            start_at=now,
            end_at=now + timedelta(days=10),
        )
        queued = self._create_proposal()

        serializer = SubmitSerializer(queued, data={
            'new_start_at': now + timedelta(days=9),
            'new_end_at': now + timedelta(days=19),
            'new_envelope_xdr': 'AAAA',
            'new_transaction_hash': 'a' * 64,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('new_start_at', serializer.errors)
        self.assertIn('new_end_at', serializer.errors)

    def test_asset_proposal_submit_rejects_when_general_proposal_interval_overlaps_voting(self):
        now = timezone.now()
        self._create_proposal(
            proposal_type=Proposal.PROPOSAL_TYPE_GENERAL,
            proposal_status=Proposal.VOTING,
            start_at=now,
            end_at=now + timedelta(days=10),
        )
        queued = self._create_proposal()

        serializer = SubmitSerializer(queued, data={
            'new_start_at': now + timedelta(days=9),
            'new_end_at': now + timedelta(days=19),
            'new_envelope_xdr': 'AAAA',
            'new_transaction_hash': 'a' * 64,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('new_start_at', serializer.errors)
        self.assertIn('new_end_at', serializer.errors)

    def test_asset_proposal_submit_allows_adjacent_interval_after_voting(self):
        now = timezone.now()
        voting_end = now + timedelta(days=10)
        self._create_proposal(
            proposal_status=Proposal.VOTING,
            start_at=now,
            end_at=voting_end,
        )
        queued = self._create_proposal()

        serializer = SubmitSerializer(queued, data={
            'new_start_at': voting_end,
            'new_end_at': voting_end + timedelta(days=10),
            'new_envelope_xdr': 'AAAA',
            'new_transaction_hash': 'a' * 64,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
