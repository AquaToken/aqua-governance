import base64
import hashlib
import json

from django.conf import settings

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from django_quill.quill import Quill
from stellar_sdk import HashMemo, Server

from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.utils.payments import check_payment


class LogVoteSerializer(serializers.ModelSerializer):

    class Meta:
        model = LogVote
        fields = ['account_issuer', 'vote_choice', 'amount', 'transaction_link', 'created_at']


class QuillField(serializers.Field):
    def get_attribute(self, instance):
        return instance.text.html

    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        obj = {'delta': '', 'html': data}
        return Quill(json.dumps(obj))


class ProposalListSerializer(serializers.ModelSerializer):
    text = QuillField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result',
            'is_simple_proposal',
        ]


class ProposalDetailSerializer(serializers.ModelSerializer):
    text = QuillField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'is_simple_proposal',
            'vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result',
        ]


class ProposalCreateSerializer(serializers.ModelSerializer):
    text = QuillField()

    class Meta:
        model = Proposal
        fields = [
            'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
        ]
        extra_kwargs = {
            'transaction_hash': {'required': True},
        }

    def validate(self, data):
        data = super(ProposalCreateSerializer, self).validate(data)

        tx_hash = data.get('transaction_hash', None)
        horizon_server = Server(settings.HORIZON_URL)
        transaction_info = horizon_server.transactions().transaction(tx_hash).call()

        if not check_payment(tx_hash):
            raise ValidationError('invalid payment')

        memo = transaction_info.get('memo', None)
        if not memo:
            raise ValidationError('memo missed')

        text_hash = hashlib.sha256(data['text'].html.encode('utf-8')).hexdigest()

        if not base64.b64encode(HashMemo(text_hash).memo_hash).decode() == memo:
            raise ValidationError('invalid memo')

        return data
