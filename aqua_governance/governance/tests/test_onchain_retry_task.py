from datetime import timedelta
import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from django_quill.quill import Quill

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.tasks import task_retry_failed_onchain_executions
from aqua_governance.governance.tests._factories import make_asset_proposal_raw


def _quill_text(html='<p>Retry me</p>'):
    return Quill(json.dumps({'delta': '', 'html': html}))


class OnchainRetryTaskTests(TestCase):
    def test_retry_failed_onchain_executions_recomputes_results_first(self):
        proposal = make_asset_proposal_raw(
            title='Retry me',
            text=_quill_text(),
            draft=False,
            action=Proposal.NONE,
            proposal_status=Proposal.VOTED,
            proposal_type=Proposal.PROPOSAL_TYPE_ADD_ASSET,
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_FAILED,
        )

        with patch('aqua_governance.governance.tasks.task_update_proposal_results') as update_mock:
            task_retry_failed_onchain_executions()

        update_mock.assert_called_once_with(proposal.id, True)

    def test_retry_failed_onchain_executions_retries_pending_proposals(self):
        proposal = make_asset_proposal_raw(
            title='Retry pending',
            text=_quill_text(),
            draft=False,
            action=Proposal.NONE,
            proposal_status=Proposal.VOTED,
            proposal_type=Proposal.PROPOSAL_TYPE_ADD_ASSET,
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_PENDING,
        )

        with patch('aqua_governance.governance.tasks.task_update_proposal_results') as update_mock:
            task_retry_failed_onchain_executions()

        update_mock.assert_called_once_with(proposal.id, True)

    def test_retry_failed_onchain_executions_marks_stale_in_progress_for_review(self):
        proposal = make_asset_proposal_raw(
            title='Retry stale',
            text=_quill_text(),
            draft=False,
            action=Proposal.NONE,
            proposal_status=Proposal.VOTED,
            proposal_type=Proposal.PROPOSAL_TYPE_ADD_ASSET,
            onchain_execution_status=Proposal.ONCHAIN_EXECUTION_IN_PROGRESS,
            onchain_execution_started_at=(
                timezone.now() - timedelta(seconds=settings.ONCHAIN_EXECUTION_LEASE_SECONDS + 1)
            ),
            onchain_execution_tx_hash=None,
        )

        with patch('aqua_governance.governance.tasks.task_update_proposal_results') as update_mock:
            task_retry_failed_onchain_executions()

        proposal.refresh_from_db()
        self.assertEqual(proposal.onchain_execution_status, Proposal.ONCHAIN_EXECUTION_REQUIRES_REVIEW)
        update_mock.assert_not_called()
