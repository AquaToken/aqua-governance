from django.db.models import Prefetch
from django.http import Http404

from rest_framework.filters import OrderingFilter
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet

from aqua_governance.governance.filters import HideFilterBackend
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.serializers import LogVoteSerializer, ProposalDetailSerializer, ProposalListSerializer


class ProposalsView(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = Proposal.objects.all().prefetch_related(
        Prefetch('logvote_set', LogVote.objects.all().order_by('-created_at')),
    )
    permission_classes = (AllowAny, )
    serializer_class = ProposalListSerializer
    filter_backends = (
        HideFilterBackend,
        OrderingFilter,
    )
    ordering = ['created_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ProposalDetailSerializer
        return super().get_serializer_class()


class LogVoteView(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = LogVote.objects.all()
    permission_classes = (AllowAny, )
    serializer_class = LogVoteSerializer
    filter_backends = (
        OrderingFilter,
    )
    ordering = ['-created_at']
    ordering_fields = ['created_at', 'amount', 'vote_choice', 'account_issuer']

    def get_queryset(self):
        proposal_id = self.request.query_params.get('proposal_id', None)
        if not proposal_id:
            raise Http404
        queryset = super(LogVoteView, self).get_queryset()

        return queryset.filter(proposal=proposal_id)
