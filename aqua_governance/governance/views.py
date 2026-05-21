from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.decorators import action

from rest_framework.filters import OrderingFilter
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet
from stellar_sdk import TransactionEnvelope

from aqua_governance.governance.filters import (
    ProposalStatusFilterBackend,
    ProposalOwnerFilterBackend,
    ProposalTypeFilterBackend,
    LogVoteOwnerFilterBackend,
    LogVoteProposalIdFilterBackend,
    ProposalVoteOwnerFilterBackend,
    build_logvote_prefetch,
    is_active_vote_query,
)
from aqua_governance.governance.models import (
    AssetProposalPayload,
    AssetToken,
    HistoryProposal,
    LogVote,
    Proposal,
)
from aqua_governance.governance.pagination import CustomPageNumberPagination
from aqua_governance.governance.serializers import (
    LogVoteSerializer,
    ProposalCreateSerializer,
    ProposalDetailSerializer,
    ProposalListSerializer,
)
from aqua_governance.governance import serializers_v2


class AssetTokenView(ListModelMixin, GenericViewSet):
    permission_classes = (AllowAny,)
    pagination_class = CustomPageNumberPagination
    serializer_class = serializers_v2.AssetTokenSerializer

    def get_queryset(self):
        visible_payloads = AssetProposalPayload.objects.select_related('proposal').filter(
            proposal__hide=False,
            proposal__draft=False,
        ).order_by('-proposal__end_at', '-proposal__created_at')
        return AssetToken.objects.filter(
            payloads__proposal__hide=False,
            payloads__proposal__draft=False,
        ).distinct().prefetch_related(
            Prefetch(
                'payloads',
                queryset=visible_payloads,
            ),
        ).order_by('-last_execution_at', '-created_at')


class ProposalsView(ListModelMixin, RetrieveModelMixin, CreateModelMixin, GenericViewSet):
    queryset = Proposal.objects.filter(
        hide=False,
        draft=False,
        created_at__lte=datetime(2022, 4, 15, tzinfo=dt_timezone.utc),
    )
    permission_classes = (AllowAny, )
    serializer_class = ProposalListSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = (
        OrderingFilter,
        ProposalTypeFilterBackend,
    )
    ordering = ['created_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ProposalDetailSerializer
        elif self.action == 'create':
            return ProposalCreateSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset().select_related('asset_payload__asset_token')
        active_only = is_active_vote_query(self.request)
        if self.action == 'list' and active_only:
            queryset = queryset.filter(logvote__hide=False, logvote__claimed=False).distinct()
        return queryset.prefetch_related(build_logvote_prefetch(self.request))


class LogVoteView(ListModelMixin, GenericViewSet):
    queryset = LogVote.objects.filter(hide=False)
    permission_classes = (AllowAny, )
    serializer_class = LogVoteSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = (
        OrderingFilter,
        LogVoteOwnerFilterBackend,
        LogVoteProposalIdFilterBackend,
    )
    ordering = ['-created_at']
    ordering_fields = ['created_at', 'amount', 'vote_choice', 'account_issuer']


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
        ProposalTypeFilterBackend,
        ProposalVoteOwnerFilterBackend,
    )
    ordering = ['created_at']

    def get_queryset(self):
        queryset = super(ProposalViewSet, self).get_queryset().select_related(
            'asset_payload__asset_token',
        ).prefetch_related(
            Prefetch('history_proposal', HistoryProposal.objects.filter(hide=False))
        )
        if self.action != 'retrieve' and self.action != 'list':
            queryset = queryset.exclude(proposal_status=Proposal.EXPIRED)

        if self.action == 'submit_proposal':
            queryset = queryset.filter(
                proposal_status=Proposal.DISCUSSION,
                last_updated_at__lte=timezone.now() - settings.DISCUSSION_TIME,
            )

        if self.action == 'update' or self.action == 'partial_update':
            queryset = queryset.filter(proposal_status=Proposal.DISCUSSION)
        if self.action == 'check_proposal_payment':
            return queryset.exclude(action=Proposal.NONE)
        queryset = queryset.filter(draft=False)
        if self.action == 'list' and is_active_vote_query(self.request):
            queryset = queryset.filter(logvote__hide=False, logvote__claimed=False).distinct()

        if self.request.query_params.get('vote_owner_public_key'):
            return queryset

        return queryset

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
