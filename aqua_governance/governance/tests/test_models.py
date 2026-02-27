from unittest.mock import patch
import json

from django.test import TestCase
from django_quill.quill import Quill

from aqua_governance.governance.models import Proposal


class ProposalModelFieldsTestCase(TestCase):
    def test_proposal_type_field_has_expected_choices(self):
        field = Proposal._meta.get_field('proposal_type')

        self.assertEqual(
            [choice[0] for choice in field.choices],
            ['GENERAL', 'ASSET_WHITELIST', 'ASSET_REVOCATION'],
        )

    def test_proposal_type_default_is_general(self):
        proposal = Proposal()

        self.assertEqual(proposal.proposal_type, 'GENERAL')

    def test_target_asset_address_default_is_none(self):
        proposal = Proposal()

        self.assertIsNone(proposal.target_asset_address)


class AssetRecordModelTestCase(TestCase):
    def test_asset_record_default_status_unknown(self):
        from aqua_governance.governance.models import AssetRecord

        asset = AssetRecord.objects.create(asset_address='G' + 'A' * 55)

        self.assertEqual(asset.status, 'unknown')

    def test_asset_record_default_ledgers_are_zero(self):
        from aqua_governance.governance.models import AssetRecord

        asset = AssetRecord.objects.create(asset_address='G' + 'B' * 55)

        self.assertEqual((asset.added_ledger, asset.updated_ledger), (0, 0))


class ProposalExecutionModelTestCase(TestCase):
    @patch('aqua_governance.governance.models.requests.get')
    def test_proposal_execution_default_status_pending(self, mock_get):
        from aqua_governance.governance.models import ProposalExecution

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'ice_supply_amount': '0'}

        proposal = Proposal.objects.create(
            proposed_by='G' + 'C' * 55,
            title='Asset proposal',
            text=Quill(json.dumps({'delta': '', 'html': '<p>Body</p>'})),
        )
        execution = ProposalExecution.objects.create(proposal=proposal)

        self.assertEqual(execution.status, 'PENDING')
