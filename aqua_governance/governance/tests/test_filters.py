import json
from unittest.mock import patch

from django.test import TestCase
from django_quill.quill import Quill
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from aqua_governance.governance.models import Proposal


class ProposalTypeFilterBackendTestCase(TestCase):
    def setUp(self):
        patcher = patch('aqua_governance.governance.models.requests.get')
        self.addCleanup(patcher.stop)
        self.mock_get = patcher.start()
        self.mock_get.return_value.status_code = 200
        self.mock_get.return_value.json.return_value = {'ice_supply_amount': '0'}

    def _create_proposal(self, proposal_type):
        return Proposal.objects.create(
            proposed_by='G' + 'A' * 55,
            title='Proposal %s' % proposal_type,
            text=Quill(json.dumps({'delta': '', 'html': '<p>Text</p>'})),
            proposal_type=proposal_type,
        )

    def test_filter_by_asset_whitelist_type(self):
        from aqua_governance.governance.filters import ProposalTypeFilterBackend

        expected_proposal = self._create_proposal(Proposal.ASSET_WHITELIST)
        self._create_proposal(Proposal.GENERAL)

        request = Request(APIRequestFactory().get('/api/proposal/', {'proposal_type': 'asset_whitelist'}))
        queryset = ProposalTypeFilterBackend().filter_queryset(request, Proposal.objects.all(), None)

        self.assertEqual(list(queryset.values_list('id', flat=True)), [expected_proposal.id])

    def test_viewset_has_proposal_type_filter_backend(self):
        from aqua_governance.governance.filters import ProposalTypeFilterBackend
        from aqua_governance.governance.views import ProposalViewSet

        self.assertIn(ProposalTypeFilterBackend, ProposalViewSet.filter_backends)
