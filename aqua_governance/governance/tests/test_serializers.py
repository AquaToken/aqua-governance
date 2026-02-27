from django.test import TestCase

from aqua_governance.governance.serializers_v2 import ProposalCreateSerializer


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
