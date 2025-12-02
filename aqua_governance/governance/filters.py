from django.db.models import Prefetch
from rest_framework.filters import BaseFilterBackend

from aqua_governance.governance.models import Proposal, LogVote


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
        claimed = not bool(request.query_params.get('active', False))
        if public_key:
            return queryset.filter(logvote__account_issuer=public_key, logvote__claimed=claimed).distinct().prefetch_related(
                Prefetch('logvote_set',
                         LogVote.objects.filter(account_issuer=public_key, claimed=claimed).order_by(
                             '-created_at')),
            )
        return queryset.prefetch_related(
            Prefetch('logvote_set', LogVote.objects.filter(claimed=claimed).order_by('-created_at')),
        )


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
