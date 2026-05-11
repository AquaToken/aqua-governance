from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from aqua_governance.governance.db_locks import acquire_asset_proposal_transition_lock
from aqua_governance.governance.models import Proposal, HistoryProposal
from aqua_governance.governance.onchain_hooks.validators import validate_asset_payload
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
ASSET_IDENTIFIER_FIELDS = (
    'asset_code',
    'asset_issuer',
    'asset_contract_address',
)
ASSET_FIELDS = ASSET_IDENTIFIER_FIELDS + ASSET_REQUIRED_TEXT_FIELDS


def _value_is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _get_quill_html(value) -> str:
    return getattr(value, 'html', '') or ''


def _find_pending_duplicate_create(attrs):
    text_html = _get_quill_html(attrs.get('text'))
    pending_proposals = Proposal.objects.filter(
        hide=False,
        draft=True,
        action=Proposal.TO_CREATE,
        proposed_by=attrs.get('proposed_by'),
        title=attrs.get('title'),
        proposal_type=attrs.get('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL),
    ).only('id', 'text')

    for proposal in pending_proposals:
        if _get_quill_html(proposal.text) == text_html:
            return proposal

    return None


class ProposalListSerializer(serializers.ModelSerializer):
    text = QuillField()
    logvote_set = LogVoteSerializer(many=True)
    payment_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'vote_for_result', 'vote_against_result',
            'is_simple_proposal', 'aqua_circulating_supply', 'proposal_status', 'payment_status',
            'payment_verification_status', 'draft', 'action',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'last_updated_at', 'created_at',
            'logvote_set', 'percent_for_quorum', 'ice_circulating_supply', 'vote_for_issuer', 'vote_against_issuer',
            'abstain_issuer', 'vote_abstain_result', 'proposal_type', 'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
            'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
        ]

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status


class ProposalDetailSerializer(serializers.ModelSerializer):
    text = QuillField()
    history_proposal = HistoryProposalSerializer(read_only=True, many=True)
    payment_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'version', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'is_simple_proposal',
            'proposal_status', 'payment_status', 'payment_verification_status', 'draft', 'action', 'last_updated_at',
            'vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result',
            'aqua_circulating_supply', 'discord_channel_url', 'discord_channel_name', 'discord_username',
            'history_proposal', 'created_at', 'percent_for_quorum', 'ice_circulating_supply',
            'abstain_issuer', 'vote_abstain_result', 'proposal_type', 'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
            'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
        ]

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status


class ProposalCreateSerializer(serializers.ModelSerializer):
    text = QuillField()
    discord_username = serializers.CharField(required=True, allow_null=True)
    payment_verification_status = serializers.SerializerMethodField()

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
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
            'proposal_status', 'payment_status', 'payment_verification_status', 'draft', 'action',
            'last_updated_at', 'created_at',
        ]
        read_only_fields = [
            'proposal_status', 'payment_status', 'draft', 'start_at', 'end_at', 'last_updated_at', 'created_at',
            'discord_channel_name', 'discord_channel_url',
            'action', 'payment_verification_status',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
        ]
        extra_kwargs = {
            'envelope_xdr': {'required': True},
            'transaction_hash': {'required': True},
        }

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status

    def validate(self, attrs):
        proposal_type = attrs.get('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL)

        if proposal_type == Proposal.PROPOSAL_TYPE_GENERAL:
            self._validate_general_payload(attrs)
        elif Proposal.is_asset_proposal_type(proposal_type):
            self._validate_asset_payload(attrs)
        else:
            raise ValidationError({'proposal_type': 'Unsupported proposal_type value.'})

        duplicate = _find_pending_duplicate_create(attrs)
        if duplicate:
            raise ValidationError({
                'non_field_errors': [
                    'A matching proposal is already waiting for payment verification. '
                    'Please wait a few minutes before trying again.',
                ],
                'proposal_id': duplicate.id,
            })

        return attrs

    def _validate_general_payload(self, attrs):
        errors = {}
        for field_name in ASSET_FIELDS:
            if not _value_is_blank(attrs.get(field_name)):
                errors[field_name] = 'General proposal does not support asset fields.'
        if errors:
            raise ValidationError(errors)

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

        try:
            validate_asset_payload(
                asset_code=asset_code,
                asset_issuer=asset_issuer,
                asset_contract_address=contract_address,
                require_onchain_verification=True,
            )
        except ValueError as exc:
            raise ValidationError(self._map_asset_validation_error(str(exc))) from exc

    @staticmethod
    def _map_asset_validation_error(message: str):
        if 'Provide both asset_code and asset_issuer together.' in message:
            return {
                'asset_code': message,
                'asset_issuer': message,
            }
        if 'Provide asset_code + asset_issuer, or asset_contract_address.' in message:
            return {
                'asset_code': message,
                'asset_issuer': message,
                'asset_contract_address': message,
            }
        if 'asset_issuer' in message:
            return {'asset_issuer': message}
        if 'asset_contract_address' in message or 'Soroban RPC' in message:
            return {'asset_contract_address': message}
        if 'Horizon' in message or 'contract_id' in message:
            return {
                'asset_code': message,
                'asset_issuer': message,
            }
        return {'proposal_type': message}

    def create(self, validated_data):
        validated_data['draft'] = True
        validated_data['action'] = Proposal.TO_CREATE
        validated_data.setdefault('proposal_type', Proposal.PROPOSAL_TYPE_GENERAL)
        if Proposal.is_asset_proposal_type(validated_data['proposal_type']):
            validated_data['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_PENDING
        else:
            validated_data['onchain_execution_status'] = Proposal.ONCHAIN_EXECUTION_NOT_REQUIRED
        status = check_transaction_xdr(validated_data, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
        if status not in (Proposal.FINE, Proposal.HORIZON_ERROR):
            validated_data['hide'] = True
        validated_data['payment_status'] = status
        return super(ProposalCreateSerializer, self).create(validated_data)


class ProposalUpdateSerializer(serializers.ModelSerializer):  # think about joining with create serializer
    text = QuillField(required=False)
    new_text = QuillField()
    discord_username = serializers.CharField(required=False, allow_null=True)
    payment_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'version', 'proposed_by', 'title', 'text', 'start_at', 'end_at', 'transaction_hash',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'payment_verification_status', 'draft', 'action',
            'last_updated_at', 'created_at',
            'proposal_type',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
            'new_envelope_xdr', 'new_transaction_hash', 'new_title', 'new_text',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'start_at', 'end_at', 'version', 'title', 'text',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'payment_verification_status', 'draft', 'action',
            'proposal_type',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
        ]
        extra_kwargs = {
            'new_title': {'required': True},
            'new_text': {'required': True},
            'new_envelope_xdr': {'required': True},
            'new_transaction_hash': {'required': True},
        }

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status

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
    payment_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposed_by', 'title', 'text', 'start_at', 'end_at',
            'discord_channel_url', 'discord_channel_name', 'discord_username', 'envelope_xdr',
            'proposal_status', 'payment_status', 'payment_verification_status', 'draft', 'action',
            'last_updated_at', 'created_at',
            'proposal_type', 'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
            'new_start_at', 'new_end_at', 'new_envelope_xdr', 'new_transaction_hash',
        ]
        read_only_fields = [
            'id', 'proposed_by', 'title', 'text',
            'discord_channel_url', 'discord_channel_name', 'discord_username',
            'proposal_status', 'payment_status', 'last_updated_at', 'created_at',
            'payment_verification_status', 'draft', 'action',
            'proposal_type', 'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
            'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
            'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
            'asset_aquarius_traction', 'asset_issuer_commitments',
            'onchain_action_type', 'onchain_action_args',
            'onchain_execution_status', 'onchain_execution_tx_hash', 'onchain_execution_started_at',
            'onchain_execution_submitted_at', 'onchain_execution_poll_count',
        ]
        extra_kwargs = {
            'new_start_at': {'required': True},
            'new_end_at': {'required': True},
            'new_envelope_xdr': {'required': True},
            'new_transaction_hash': {'required': True},
        }

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status

    def validate(self, attrs):
        new_start_at = attrs['new_start_at']
        new_end_at = attrs['new_end_at']
        if new_end_at <= new_start_at:
            raise ValidationError({'new_end_at': 'new_end_at must be greater than new_start_at.'})
        if new_end_at <= timezone.now():
            raise ValidationError({'new_end_at': 'new_end_at must be in the future.'})

        is_asset_proposal = self.instance.is_asset_proposal
        minimum_days = (
            settings.ASSET_MIN_VOTING_DURATION_DAYS
            if is_asset_proposal
            else settings.DEFAULT_MIN_VOTING_DURATION_DAYS
        )
        if (new_end_at - new_start_at) < timedelta(days=minimum_days):
            raise ValidationError({
                'new_end_at': f'Minimum voting duration for this proposal type is {minimum_days} days.',
            })

        if is_asset_proposal and self._has_asset_voting_interval_conflict(
            new_start_at,
            new_end_at,
            self.instance.id,
        ):
            raise self._asset_voting_interval_conflict_error()
        return attrs

    @staticmethod
    def _has_asset_voting_interval_conflict(new_start_at, new_end_at, current_proposal_id: int) -> bool:
        return Proposal.has_asset_voting_interval_conflict(
            start_at=new_start_at,
            end_at=new_end_at,
            current_proposal_id=current_proposal_id,
        )

    @staticmethod
    def _asset_voting_interval_conflict_error() -> ValidationError:
        return ValidationError({
            'new_start_at': 'Asset proposal voting interval overlaps with another queued or active asset proposal.',
            'new_end_at': 'Asset proposal voting interval overlaps with another queued or active asset proposal.',
        })

    def update(self, instance, validated_data):
        validated_data['action'] = Proposal.TO_SUBMIT
        data_to_check = {'text': instance.text, 'envelope_xdr': validated_data['new_envelope_xdr']}
        status = check_transaction_xdr(data_to_check, settings.PROPOSAL_SUBMIT_COST)
        validated_data['payment_status'] = status

        with transaction.atomic():
            if instance.is_asset_proposal:
                acquire_asset_proposal_transition_lock()
            locked_instance = Proposal.objects.select_for_update().get(id=instance.id)
            if (
                locked_instance.is_asset_proposal
                and self._has_asset_voting_interval_conflict(
                    validated_data['new_start_at'],
                    validated_data['new_end_at'],
                    locked_instance.id,
                )
            ):
                raise self._asset_voting_interval_conflict_error()
            return super(SubmitSerializer, self).update(locked_instance, validated_data)


class AssetTokenProposalSerializer(serializers.ModelSerializer):
    payment_verification_status = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            'id', 'proposal_type', 'proposal_status', 'payment_status', 'payment_verification_status',
            'draft', 'action', 'title',
            'start_at', 'end_at', 'new_start_at', 'new_end_at',
            'vote_for_result', 'vote_against_result', 'vote_abstain_result',
            'onchain_execution_status', 'onchain_execution_tx_hash',
            'created_at', 'last_updated_at',
        ]

    def get_payment_verification_status(self, obj):
        return obj.payment_verification_status


class AssetTokenSerializer(serializers.Serializer):
    asset_code = serializers.CharField(allow_null=True)
    asset_issuer = serializers.CharField(allow_null=True)
    asset_contract_address = serializers.CharField(allow_null=True)
    whitelisted = serializers.BooleanField()
    proposals = AssetTokenProposalSerializer(many=True)
