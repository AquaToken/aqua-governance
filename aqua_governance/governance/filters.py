from typing import Optional

from django.db.models import Prefetch
from rest_framework.filters import BaseFilterBackend

from aqua_governance.governance.models import Proposal, LogVote

ACTIVE_QUERY_VALUES = {'1', 'true', 'yes', 'on'}


def is_active_vote_query(request) -> bool:
    active_value = request.query_params.get('active')
    if active_value is None:
        return False
    return active_value.strip().lower() in ACTIVE_QUERY_VALUES


def build_logvote_prefetch(request, public_key: Optional[str] = None) -> Prefetch:
    queryset = LogVote.objects.filter(hide=False)
    if public_key:
        queryset = queryset.filter(account_issuer=public_key)
    if is_active_vote_query(request):
        queryset = queryset.filter(claimed=False)
    return Prefetch('logvote_set', queryset.order_by('-created_at'))


def apply_vote_owner_queryset_filters(queryset, request, public_key: str):
    queryset = queryset.filter(logvote__account_issuer=public_key, logvote__hide=False)
    if is_active_vote_query(request):
        queryset = queryset.filter(logvote__claimed=False)
    return queryset.distinct()


class HideFilterBackend(BaseFilterBackend):  # TODO: remove it
    def filter_queryset(self, request, queryset, view):
        hide_value = request.query_params.get('hide')

        if not hide_value or hide_value == 'false':
            return queryset.filter(hide=False)

        if hide_value == 'true':
            return queryset.filter(hide=True)

        if hide_value == 'all':
            return queryset

        return queryset.filter(hide=False)


class ProposalStatusFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        status_value = request.query_params.get('status')

        if status_value == 'discussion':
            return queryset.filter(proposal_status=Proposal.DISCUSSION)

        if status_value == 'voting':
            return queryset.filter(proposal_status=Proposal.VOTING)

        if status_value == 'voted':
            return queryset.filter(proposal_status=Proposal.VOTED)

        if status_value == 'expired':
            return queryset.filter(proposal_status=Proposal.EXPIRED)

        return queryset


class ProposalOwnerFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        public_key = request.query_params.get('owner_public_key')

        if public_key:
            return queryset.filter(proposed_by=public_key)

        return queryset


class ProposalVoteOwnerFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        public_key = request.query_params.get('vote_owner_public_key')
        if public_key:
            queryset = apply_vote_owner_queryset_filters(queryset, request, public_key)
            return queryset.prefetch_related(build_logvote_prefetch(request, public_key=public_key))
        return queryset.prefetch_related(build_logvote_prefetch(request))


class LogVoteOwnerFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        public_key = request.query_params.get('owner_public_key')
        if public_key:
            return queryset.filter(account_issuer=public_key)
        return queryset


class LogVoteProposalIdFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        proposal_id = request.query_params.get('proposal_id', None)
        if proposal_id:
            return queryset.filter(proposal=proposal_id)
        return queryset
