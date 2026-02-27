from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.serializers_v2 import ProposalCreateSerializer, SubmitSerializer


class ProposalCreateSerializerTestCase(TestCase):
    def _get_base_data(self):
        return {
            'proposed_by': 'G' + 'A' * 55,
            'title': 'Proposal title',
            'text': '<p>Proposal text</p>',
            'transaction_hash': 'a' * 64,
            'envelope_xdr': 'envelope',
            'discord_username': 'discord-user',
        }

    def test_target_asset_address_is_required_for_asset_whitelist_type(self):
        data = self._get_base_data()
        data['proposal_type'] = 'ASSET_WHITELIST'

        serializer = ProposalCreateSerializer(data=data)
        serializer.is_valid()

        self.assertIn('target_asset_address', serializer.errors)

    def test_target_asset_address_is_not_required_for_general_type(self):
        serializer = ProposalCreateSerializer(data=self._get_base_data())

        self.assertTrue(serializer.is_valid())

    def test_target_asset_address_is_allowed_for_asset_revocation_type(self):
        data = self._get_base_data()
        data['proposal_type'] = 'ASSET_REVOCATION'
        data['target_asset_address'] = 'G' + 'B' * 55

        serializer = ProposalCreateSerializer(data=data)

        self.assertTrue(serializer.is_valid())


class SubmitSerializerTestCase(TestCase):
    def _build_proposal(self, proposal_type):
        proposal = Proposal(
            proposed_by='G' + 'A' * 55,
            title='Proposal title',
            text='<p>Proposal text</p>',
            proposal_type=proposal_type,
        )
        if proposal_type != Proposal.GENERAL:
            proposal.target_asset_address = 'G' + 'B' * 55
        return proposal

    def _get_submit_data(self, start_at, end_at):
        return {
            'new_start_at': start_at,
            'new_end_at': end_at,
            'new_envelope_xdr': 'new-envelope',
            'new_transaction_hash': 'b' * 64,
        }

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_asset_proposal_rejects_voting_period_under_10_days(self, _mock_check_transaction_xdr):
        start_at = timezone.now()
        end_at = start_at + timedelta(days=9)
        proposal = self._build_proposal(Proposal.ASSET_WHITELIST)

        serializer = SubmitSerializer(instance=proposal, data=self._get_submit_data(start_at, end_at))

        self.assertFalse(serializer.is_valid())
        self.assertIn('new_end_at', serializer.errors)

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_asset_proposal_accepts_voting_period_of_exactly_10_days(self, _mock_check_transaction_xdr):
        start_at = timezone.now()
        end_at = start_at + timedelta(days=10)
        proposal = self._build_proposal(Proposal.ASSET_WHITELIST)

        serializer = SubmitSerializer(instance=proposal, data=self._get_submit_data(start_at, end_at))

        self.assertTrue(serializer.is_valid())

    @patch('aqua_governance.governance.serializers_v2.check_transaction_xdr', return_value=Proposal.FINE)
    def test_submit_general_proposal_allows_short_voting_period(self, _mock_check_transaction_xdr):
        start_at = timezone.now()
        end_at = start_at + timedelta(days=3)
        proposal = self._build_proposal(Proposal.GENERAL)

        serializer = SubmitSerializer(instance=proposal, data=self._get_submit_data(start_at, end_at))

        self.assertTrue(serializer.is_valid())
