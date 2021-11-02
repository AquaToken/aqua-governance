from django.db.models import Prefetch
from rest_framework.filters import OrderingFilter
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet

from aqua_governance.governance.filters import HideFilterBackend
from aqua_governance.governance.models import Proposal, LogVote
from aqua_governance.governance.serializers import ProposalDetailSerializer, ProposalListSerializer


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
    ordering = ['created_at', 'id']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ProposalDetailSerializer
        return super().get_serializer_class()
