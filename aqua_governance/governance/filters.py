from rest_framework.filters import BaseFilterBackend

from aqua_governance.governance.models import Proposal


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

        return queryset


class ProposalOwnerFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        public_key = request.query_params.get('owner_public_key')

        if public_key:
            return queryset.filter(proposed_by=public_key)

        return queryset
