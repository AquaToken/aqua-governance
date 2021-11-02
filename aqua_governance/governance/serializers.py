from rest_framework import serializers

from aqua_governance.governance.models import LogVote, Proposal


class LogVoteSerializer(serializers.ModelSerializer):

    class Meta:
        model = LogVote
        fields = ['account_issuer', 'vote_choice', 'amount']


class ProposalListSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = ['id', 'proposed_by', 'title', 'text', 'start_at', 'end_at']


class ProposalDetailSerializer(serializers.ModelSerializer):
    logvote_set = LogVoteSerializer(many=True, read_only=True)

    class Meta:
        model = Proposal
        fields = ['id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'logvote_set']
