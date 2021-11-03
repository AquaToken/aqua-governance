from rest_framework import serializers

from aqua_governance.governance.models import LogVote, Proposal


class LogVoteSerializer(serializers.ModelSerializer):

    class Meta:
        model = LogVote
        fields = ['account_issuer', 'vote_choice', 'amount']


class ProposalListSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = ['id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result']


class ProposalDetailSerializer(serializers.ModelSerializer):
    logvote_set = LogVoteSerializer(many=True, read_only=True)

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at',
            'vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result',  'logvote_set',
        ]
