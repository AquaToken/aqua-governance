from rest_framework import serializers

from aqua_governance.governance.models import LogVote, Proposal


class LogVoteSerializer(serializers.ModelSerializer):

    class Meta:
        model = LogVote
        fields = ['account_issuer', 'vote_choice', 'amount', 'transaction_link']


class ProposalListSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result',
            'is_simple_proposal',
        ]


class ProposalDetailSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'is_simple_proposal',
            'vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result',
        ]
