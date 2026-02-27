import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from django_quill.quill import Quill

from aqua_governance.governance.models import AssetRecord, Proposal, ProposalExecution


class AssetTasksTestCase(TestCase):
    def setUp(self):
        patcher = patch('aqua_governance.governance.models.requests.get')
        self.addCleanup(patcher.stop)
        self.mock_get = patcher.start()
        self.mock_get.return_value.status_code = 200
        self.mock_get.return_value.json.return_value = {'ice_supply_amount': '0'}

    def _create_asset_proposal(
        self,
        proposal_type=Proposal.ASSET_WHITELIST,
        vote_for=Decimal('60'),
        vote_against=Decimal('10'),
        aqua_supply=Decimal('0'),
        ice_supply=Decimal('100'),
        quorum=10,
    ):
        proposal = Proposal.objects.create(
            proposed_by='G' + 'A' * 55,
            title='Asset proposal',
            text=Quill(json.dumps({'delta': '', 'html': '<p>Asset proposal</p>'})),
            proposal_status=Proposal.VOTED,
            proposal_type=proposal_type,
            target_asset_address='G' + 'B' * 55,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now(),
        )
        proposal.vote_for_result = vote_for
        proposal.vote_against_result = vote_against
        proposal.aqua_circulating_supply = aqua_supply
        proposal.ice_circulating_supply = ice_supply
        proposal.percent_for_quorum = quorum
        proposal.save(
            update_fields=[
                'vote_for_result',
                'vote_against_result',
                'aqua_circulating_supply',
                'ice_circulating_supply',
                'percent_for_quorum',
            ]
        )
        return proposal

    @patch('aqua_governance.governance.tasks.soroban.set_asset_status')
    @patch('aqua_governance.governance.tasks.task_sync_asset_registry.delay')
    def test_execute_asset_proposals_marks_success_for_passed_proposal(self, mock_delay, mock_set_asset_status):
        from aqua_governance.governance.tasks import task_execute_asset_proposals

        mock_set_asset_status.return_value = 'b' * 64
        proposal = self._create_asset_proposal()

        task_execute_asset_proposals()
        execution = ProposalExecution.objects.get(proposal=proposal)

        self.assertEqual(execution.status, ProposalExecution.SUCCESS)

    @patch('aqua_governance.governance.tasks.soroban.set_asset_status')
    def test_execute_asset_proposals_uses_status_code_two_for_revocation(self, mock_set_asset_status):
        from aqua_governance.governance.tasks import task_execute_asset_proposals

        mock_set_asset_status.return_value = 'c' * 64
        self._create_asset_proposal(proposal_type=Proposal.ASSET_REVOCATION)

        task_execute_asset_proposals()

        self.assertEqual(mock_set_asset_status.call_args[0][1], 2)

    @patch('aqua_governance.governance.tasks.soroban.set_asset_status')
    def test_execute_asset_proposals_marks_skipped_when_not_passed(self, mock_set_asset_status):
        from aqua_governance.governance.tasks import task_execute_asset_proposals

        proposal = self._create_asset_proposal(vote_for=Decimal('10'), vote_against=Decimal('5'), ice_supply=Decimal('1000'), quorum=50)

        task_execute_asset_proposals()
        execution = ProposalExecution.objects.get(proposal=proposal)

        self.assertEqual(execution.status, ProposalExecution.SKIPPED)

    @patch('aqua_governance.governance.tasks.soroban.set_asset_status')
    def test_execute_asset_proposals_marks_failed_on_exception(self, mock_set_asset_status):
        from aqua_governance.governance.tasks import task_execute_asset_proposals

        mock_set_asset_status.side_effect = Exception('soroban boom')
        proposal = self._create_asset_proposal()

        task_execute_asset_proposals()
        execution = ProposalExecution.objects.get(proposal=proposal)

        self.assertEqual(execution.status, ProposalExecution.FAILED)

    @override_settings(ASSET_REGISTRY_CONTRACT_ADDRESS='')
    @patch('aqua_governance.governance.tasks.soroban.fetch_registry_page')
    def test_sync_asset_registry_skips_without_contract_address(self, mock_fetch_registry_page):
        from aqua_governance.governance.tasks import task_sync_asset_registry

        task_sync_asset_registry()

        self.assertFalse(mock_fetch_registry_page.called)

    @override_settings(ASSET_REGISTRY_CONTRACT_ADDRESS='contract-id', REGISTRY_SYNC_PAGE_LIMIT=2)
    @patch('aqua_governance.governance.tasks.soroban.fetch_registry_page')
    def test_sync_asset_registry_saves_records_from_pages(self, mock_fetch_registry_page):
        from aqua_governance.governance.tasks import task_sync_asset_registry

        mock_fetch_registry_page.side_effect = [
            [
                {
                    'asset_address': 'G' + 'C' * 55,
                    'status': 'allowed',
                    'added_ledger': 10,
                    'updated_ledger': 20,
                    'last_proposal_id': 1,
                    'meta_hash': '1' * 64,
                },
                {
                    'asset_address': 'G' + 'D' * 55,
                    'status': 'denied',
                    'added_ledger': 11,
                    'updated_ledger': 21,
                    'last_proposal_id': 2,
                    'meta_hash': '2' * 64,
                },
            ],
            [
                {
                    'asset_address': 'G' + 'E' * 55,
                    'status': 'unknown',
                    'added_ledger': 12,
                    'updated_ledger': 22,
                    'last_proposal_id': 3,
                    'meta_hash': '3' * 64,
                },
            ],
            [],
        ]

        task_sync_asset_registry()

        self.assertEqual(AssetRecord.objects.count(), 3)
