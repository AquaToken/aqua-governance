from datetime import datetime, timedelta

from django.conf import settings

from rest_framework import serializers

from aqua_governance.governance.models import Proposal, HistoryProposal
from aqua_governance.governance.serializer_fields import QuillField
from aqua_governance.governance.serializers import HistoryProposalSerializer
from aqua_governance.governance.validators import DiscordUsernameValidator
from aqua_governance.utils.payments import check_transaction_xdr


class ProposalListSerializer(serializers.ModelSerializer):
    text = QuillField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result',
            'is_simple_proposal', 'aqua_circulating_supply', 'proposal_status', 'payment_status',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'last_updated_at',
        ]


class ProposalDetailSerializer(serializers.ModelSerializer):
    text = QuillField()
    history_proposal = HistoryProposalSerializer(read_only=True, many=True)

    class Meta:
        model = Proposal
        fields = [
            'id', 'version', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'is_simple_proposal',
            'proposal_status', 'payment_status', 'last_updated_at',
            'vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result',
            'aqua_circulating_supply', 'discord_channel_url', 'discord_channel_name', 'discord_username',
            'history_proposal',
        ]


class ProposalCreateSerializer(serializers.ModelSerializer):
    text = QuillField()
    discord_username = serializers.CharField(required=True, allow_null=True, validators=[DiscordUsernameValidator(), ])

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_name', 'discord_username', 'envelope_xdr', 'discord_channel_url',
            'proposal_status', 'payment_status', 'draft', 'last_updated_at',
        ]
        read_only_fields = [
            'proposal_status', 'payment_status', 'draft', 'start_at', 'end_at', 'last_updated_at',
        ]
        extra_kwargs = {
            'envelope_xdr': {'required': True},
            'transaction_hash': {'required': True},
        }

    def create(self, validated_data):
        validated_data['draft'] = True
        status = check_transaction_xdr(validated_data, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
        if status != Proposal.FINE:
            validated_data['hide'] = True
        validated_data['payment_status'] = status
        return super(ProposalCreateSerializer, self).create(validated_data)


class ProposalUpdateSerializer(serializers.ModelSerializer):  # think about joining with create serializer
    text = QuillField()
    discord_username = serializers.CharField(required=False, allow_null=True, validators=[DiscordUsernameValidator(), ])

    class Meta:
        model = Proposal
        fields = [
            'id', 'version', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'last_updated_at',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'start_at', 'end_at', 'version',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at',
        ]
        extra_kwargs = {
            'envelope_xdr': {'required': True},
            'transaction_hash': {'required': True},
        }

    def update(self, instance, validated_data):
        HistoryProposal.objects.create(
            version=instance.version,
            title=instance.title,
            text=instance.text,
            transaction_hash=instance.transaction_hash,
            envelope_xdr=instance.envelope_xdr,
            proposal=instance,
        )
        validated_data['draft'] = True
        validated_data['version'] = instance.version + 1
        validated_data['last_updated_at'] = datetime.now()
        status = check_transaction_xdr(validated_data, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
        if status != Proposal.FINE:
            validated_data['hide'] = True
        validated_data['payment_status'] = status
        return super(ProposalUpdateSerializer, self).update(instance, validated_data)


class SubmitSerializer(serializers.ModelSerializer):
    text = QuillField(required=False)

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'last_updated_at',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'title', 'text',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at',
        ]
        extra_kwargs = {
            'start_at': {'required': True},
            'end_at': {'required': True},
            'envelope_xdr': {'required': True},
            'transaction_hash': {'required': True},
        }

    def update(self, instance, validated_data):
        HistoryProposal.objects.create(
            version=instance.version,
            hide=True,
            title=instance.title,
            text=instance.text,
            transaction_hash=instance.transaction_hash,
            envelope_xdr=instance.envelope_xdr,
            proposal=instance,
        )
        validated_data['draft'] = True
        data_to_check = {'text': instance.text}
        data_to_check.update(validated_data)
        status = check_transaction_xdr(data_to_check, settings.PROPOSAL_SUBMIT_COST)
        if status != Proposal.FINE:
            validated_data['hide'] = True
        validated_data['proposal_status'] = Proposal.VOTING
        validated_data['payment_status'] = status
        return super(SubmitSerializer, self).update(instance, validated_data)
