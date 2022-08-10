from datetime import datetime

from django.conf import settings
from django.db.models import Prefetch
from django.http import Http404
from rest_framework import exceptions
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.decorators import action

from rest_framework.filters import OrderingFilter
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet
from stellar_sdk import TransactionEnvelope

from aqua_governance.governance.filters import ProposalStatusFilterBackend, ProposalOwnerFilterBackend
from aqua_governance.governance.models import LogVote, Proposal, HistoryProposal
from aqua_governance.governance.pagination import CustomPageNumberPagination
from aqua_governance.governance.serializers import (
    LogVoteSerializer,
    ProposalCreateSerializer,
    ProposalDetailSerializer,
    ProposalListSerializer,
)
from aqua_governance.governance import serializers_v2


class ProposalsView(ListModelMixin, RetrieveModelMixin, CreateModelMixin, GenericViewSet):
    queryset = Proposal.objects.filter(hide=False, draft=False, created_at__lte=datetime(2022, 4, 15)).prefetch_related(
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


class ProposalViewSet(
    ListModelMixin,
    UpdateModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    queryset = Proposal.objects.filter(hide=False).exclude(id=65)
    permission_classes = (AllowAny, )
    serializer_class = serializers_v2.ProposalDetailSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = (
        OrderingFilter,
        ProposalStatusFilterBackend,
        ProposalOwnerFilterBackend,
    )
    ordering = ['created_at']

    def get_queryset(self):
        queryset = super(ProposalViewSet, self).get_queryset().prefetch_related(
            Prefetch('history_proposal', HistoryProposal.objects.filter(hide=False))
        )

        if self.action == 'submit_proposal':
            queryset = queryset.filter(
                proposal_status=Proposal.DISCUSSION, last_updated_at__lte=datetime.now() - settings.DISCUSSION_TIME,
            )

        if self.action == 'update' or self.action == 'partial_update':
            queryset = queryset.filter(proposal_status=Proposal.DISCUSSION)
        if self.action == 'check_proposal_payment':
            return queryset.exclude(action=Proposal.NONE)
        return queryset.filter(draft=False)

    def get_serializer_class(self):
        if self.action == 'list':
            return serializers_v2.ProposalListSerializer
        if self.action == 'update' or self.action == 'partial_update':
            return serializers_v2.ProposalUpdateSerializer
        if self.action == 'create':
            return serializers_v2.ProposalCreateSerializer
        if self.action == 'retrieve':
            return serializers_v2.ProposalDetailSerializer

        return super().get_serializer_class()

    def _check_owner_permissions(self, proposal, data):
        envelope_xdr = data.get('new_envelope_xdr', None)
        try:
            transaction_envelope = TransactionEnvelope.from_xdr(envelope_xdr, settings.NETWORK_PASSPHRASE)
        except Exception:
            raise PermissionDenied(detail='Horizon connection error ')

        if transaction_envelope.transaction.source.account_id != proposal.proposed_by:
            raise PermissionDenied(detail='You are not the proposal owner')

    def perform_update(self, serializer):
        instance = self.get_object()
        self._check_owner_permissions(instance, serializer.validated_data)
        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        # disable partial update
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='submit', url_name='submit-proposal')
    def submit_proposal(self, request, pk=None):
        proposal = self.get_object()
        serializer = serializers_v2.SubmitSerializer(proposal, data=request.data)
        serializer.is_valid(raise_exception=True)
        self._check_owner_permissions(proposal, request.data)
        serializer.save()
        return Response(data=serializer.data)

    @action(detail=True, methods=['post'], url_path='check_payment', url_name='check-payment')
    def check_proposal_payment(self, request, pk=None):
        proposal = self.get_object()
        proposal.check_transaction()
        return Response(data=self.get_serializer(instance=proposal).data)


class TestProposalViewSet(ProposalViewSet):
    queryset = Proposal.objects.filter(hide=False)
