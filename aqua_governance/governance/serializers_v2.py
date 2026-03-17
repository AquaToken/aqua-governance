from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from aqua_governance.governance.models import Proposal, HistoryProposal
from aqua_governance.governance.onchain_hooks.validators import normalize_asset_addresses
from aqua_governance.governance.serializer_fields import QuillField
from aqua_governance.governance.serializers import HistoryProposalSerializer, LogVoteSerializer
from aqua_governance.utils.payments import check_transaction_xdr

ASSET_REQUIRED_TEXT_FIELDS = (
    'asset_issuer_information',
    'asset_token_description',
    'asset_holder_distribution',
    'asset_liquidity',
    'asset_trading_volume',
    'asset_audit_info',
    'asset_stellar_flags',
    'asset_related_projects',
    'asset_community_references',
    'asset_aquarius_traction',
    'asset_issuer_commitments',
)


def _value_is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class ProposalListSerializer(serializers.ModelSerializer):
    text = QuillField()
    logvote_set = LogVoteSerializer(many=True)

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result',
            'is_simple_proposal', 'aqua_circulating_supply', 'proposal_status', 'payment_status',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'last_updated_at', 'created_at',
            'logvote_set', 'percent_for_quorum', 'ice_circulating_supply', 'vote_for_issuer', 'vote_against_issuer',
            'abstain_issuer', 'vote_abstain_result', 'proposal_type', 'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
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
            'history_proposal', 'created_at', 'percent_for_quorum', 'ice_circulating_supply',
            'abstain_issuer', 'vote_abstain_result', 'proposal_type', 'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
            'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
        ]


class ProposalCreateSerializer(serializers.ModelSerializer):
    text = QuillField()
    discord_username = serializers.CharField(required=True, allow_null=True)

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_name', 'discord_username', 'envelope_xdr', 'discord_channel_url',
            'proposal_type', 'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
            'proposal_status', 'payment_status', 'draft', 'last_updated_at', 'created_at',
        ]
        read_only_fields = [
            'proposal_status', 'payment_status', 'draft', 'start_at', 'end_at', 'last_updated_at', 'created_at',
            'discord_channel_name', 'discord_channel_url',
            'onchain_execution_status', 'onchain_execution_tx_hash',
        ]
        extra_kwargs = {
            'envelope_xdr': {'required': True},
            'transaction_hash': {'required': True},
        }

    def validate(self, attrs):
        proposal_type = attrs.get('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL)
        onchain_action_type = attrs.get('onchain_action_type', Proposal.ONCHAIN_ACTION_NONE)
        onchain_action_args = attrs.get('onchain_action_args', [])

        if proposal_type == Proposal.PROPOSAL_TYPE_GENERAL:
            if onchain_action_type != Proposal.ONCHAIN_ACTION_NONE:
                raise ValidationError({'onchain_action_type': 'General proposal does not support onchain action.'})
            if onchain_action_args:
                raise ValidationError({'onchain_action_args': 'Args must be empty when onchain action is NONE.'})
        elif proposal_type == Proposal.PROPOSAL_TYPE_ASSET:
            if onchain_action_type not in (
                Proposal.ONCHAIN_ACTION_ADD_ASSET,
                Proposal.ONCHAIN_ACTION_REMOVE_ASSET,
            ):
                raise ValidationError({
                    'onchain_action_type': 'Asset proposal requires ADD_ASSET or REMOVE_ASSET action.',
                })
            if not onchain_action_args:
                raise ValidationError({'onchain_action_args': 'Args are required for selected onchain action.'})
            self._validate_asset_payload(attrs)
        else:
            raise ValidationError({'proposal_type': 'Unsupported proposal_type value.'})

        if onchain_action_type in (Proposal.ONCHAIN_ACTION_ADD_ASSET, Proposal.ONCHAIN_ACTION_REMOVE_ASSET):
            try:
                attrs['onchain_action_args'] = normalize_asset_addresses(onchain_action_args)
            except ValueError as exc:
                raise ValidationError({'onchain_action_args': str(exc)}) from exc
        return attrs

    def _validate_asset_payload(self, attrs):
        asset_code = attrs.get('asset_code')
        asset_issuer = attrs.get('asset_issuer')
        contract_address = attrs.get('asset_contract_address')
        has_classic_asset = not _value_is_blank(asset_code) and not _value_is_blank(asset_issuer)
        has_contract_asset = not _value_is_blank(contract_address)
        if not has_classic_asset and not has_contract_asset:
            raise ValidationError({
                'asset_code': 'Provide asset_code + asset_issuer, or asset_contract_address.',
                'asset_contract_address': 'Provide asset_code + asset_issuer, or asset_contract_address.',
            })

        errors = {}
        for field_name in ASSET_REQUIRED_TEXT_FIELDS:
            if _value_is_blank(attrs.get(field_name)):
                errors[field_name] = 'This field is required for asset proposal.'
        if errors:
            raise ValidationError(errors)

    def create(self, validated_data):
        validated_data['draft'] = True
        validated_data['action'] = Proposal.TO_CREATE
        validated_data.setdefault('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL)
        validated_data.setdefault('onchain_action_args', [])
        if validated_data.get('onchain_action_type', Proposal.ONCHAIN_ACTION_NONE) == Proposal.ONCHAIN_ACTION_NONE:
            validated_data['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_NOT_REQUIRED
        else:
            validated_data['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_PENDING
        status = check_transaction_xdr(validated_data, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
        if status != Proposal.FINE:
            validated_data['hide'] = True
        validated_data['payment_status'] = status
        return super(ProposalCreateSerializer, self).create(validated_data)


class ProposalUpdateSerializer(serializers.ModelSerializer):  # think about joining with create serializer
    text = QuillField(required=False)
    new_text = QuillField()
    discord_username = serializers.CharField(required=False, allow_null=True)

    class Meta:
        model = Proposal
        fields = [
            'id', 'version', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'proposal_type',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
            'new_envelope_xdr', 'new_transaction_hash', 'new_title', 'new_text',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'start_at', 'end_at', 'version', 'title', 'text',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'proposal_type',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
        ]
        extra_kwargs = {
            'new_title': {'required': True},
            'new_text': {'required': True},
            'new_envelope_xdr': {'required': True},
            'new_transaction_hash': {'required': True},
        }

    def update(self, instance, validated_data):
        validated_data['action'] = Proposal.TO_UPDATE
        data_to_check = {
            'text': validated_data['new_text'], 'envelope_xdr': validated_data['new_envelope_xdr'],
        }

        status = check_transaction_xdr(data_to_check, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
        validated_data['payment_status'] = status
        return super(ProposalUpdateSerializer, self).update(instance, validated_data)


class SubmitSerializer(serializers.ModelSerializer):
    text = QuillField(required=False)

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'proposal_type', 'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
            'new_start_at', 'new_end_at', 'new_envelope_xdr', 'new_transaction_hash',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'title', 'text',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'proposal_type', 'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash',
        ]
        extra_kwargs = {
            'new_start_at': {'required': True},
            'new_end_at': {'required': True},
            'new_envelope_xdr': {'required': True},
            'new_transaction_hash': {'required': True},
        }

    def validate(self, attrs):
        new_start_at = attrs['new_start_at']
        new_end_at = attrs['new_end_at']
        if new_end_at <= new_start_at:
            raise ValidationError({'new_end_at': 'new_end_at must be greater than new_start_at.'})

        is_asset_proposal = self.instance.proposal_type == Proposal.PROPOSAL_TYPE_ASSET
        minimum_days = (
            settings.ASSET_MIN_VOTING_DURATION_DAYS
            if is_asset_proposal
            else settings.DEFAULT_MIN_VOTING_DURATION_DAYS
        )
        if (new_end_at - new_start_at) < timedelta(days=minimum_days):
            raise ValidationError({
                'new_end_at': f'Minimum voting duration for this proposal type is {minimum_days} days.',
            })

        if is_asset_proposal and self._has_active_asset_proposal_conflict(self.instance.id):
            raise ValidationError({
                'proposal_type': 'Another active asset proposal already exists. Submit is blocked.',
            })
        return attrs

    @staticmethod
    def _has_active_asset_proposal_conflict(current_proposal_id: int) -> bool:
        return Proposal.objects.filter(
            proposal_type=Proposal.PROPOSAL_TYPE_ASSET,
            hide=False,
            draft=False,
        ).exclude(
            id=current_proposal_id,
        ).filter(
            Q(proposal_status__in=(Proposal.DISCUSSION, Proposal.VOTING)) | Q(action=Proposal.TO_SUBMIT),
        ).exists()

    def update(self, instance, validated_data):
        validated_data['action'] = Proposal.TO_SUBMIT
        data_to_check = {'text': instance.text, 'envelope_xdr': validated_data['new_envelope_xdr']}
        status = check_transaction_xdr(data_to_check, settings.PROPOSAL_SUBMIT_COST)
        validated_data['payment_status'] = status

        with transaction.atomic():
            locked_instance = Proposal.objects.select_for_update().get(id=instance.id)
            if (
                locked_instance.proposal_type == Proposal.PROPOSAL_TYPE_ASSET
                and self._has_active_asset_proposal_conflict(locked_instance.id)
            ):
                raise ValidationError({
                    'proposal_type': 'Another active asset proposal already exists. Submit is blocked.',
                })
            return super(SubmitSerializer, self).update(locked_instance, validated_data)
