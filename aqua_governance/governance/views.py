from django.db.models import Prefetch
from django.http import Http404

from rest_framework.filters import OrderingFilter
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet

from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.pagination import CustomPageNumberPagination
from aqua_governance.governance.serializers import (
    LogVoteSerializer,
    ProposalCreateSerializer,
    ProposalDetailSerializer,
    ProposalListSerializer, ProposalCreateSerializerV2, ProposalUpdateSerializer,
)


class ProposalsView(ListModelMixin, RetrieveModelMixin, CreateModelMixin, GenericViewSet):
    queryset = Proposal.objects.filter(hide=False).prefetch_related(
        Prefetch('logvote_set', LogVote.objects.all().order_by('-created_at')),
    )
    permission_classes = (AllowAny, )
    serializer_class = ProposalListSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = (
        OrderingFilter,
    )
    ordering = ['created_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ProposalDetailSerializer
        elif self.action == 'create':
            return ProposalCreateSerializer
        return super().get_serializer_class()


class LogVoteView(ListModelMixin, GenericViewSet):
    queryset = LogVote.objects.all()
    permission_classes = (AllowAny, )
    serializer_class = LogVoteSerializer
    pagination_class = CustomPageNumberPagination
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


class ProposalCreateViewSet(UpdateModelMixin, CreateModelMixin, GenericViewSet):
    queryset = Proposal.objects.filter(draft=True)
    permission_classes = (AllowAny, )
    lookup_field = 'transaction_hash'
    serializer_class = ProposalListSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = (
        OrderingFilter,
    )
    ordering = ['created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return ProposalCreateSerializerV2
        if self.action == 'update' or self.action == 'partial_update':
            return ProposalUpdateSerializer
        return super().get_serializer_class()


