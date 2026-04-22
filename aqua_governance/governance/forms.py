from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.onchain_hooks.validators import validate_asset_payload
from aqua_governance.governance.serializers_v2 import ASSET_FIELDS, ASSET_REQUIRED_TEXT_FIELDS
from aqua_governance.utils.payments import check_transaction_xdr
from aqua_governance.utils.widgets import CustomQuillWidget


def _value_is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class ProposalAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['text'].widget = CustomQuillWidget()
        if 'new_text' in self.fields:
            self.fields['new_text'].widget = CustomQuillWidget()

        for field_name in ('transaction_hash', 'new_transaction_hash'):
            if field_name in self.fields:
                self.fields[field_name].widget = forms.TextInput()

        for field_name in ('envelope_xdr', 'new_envelope_xdr'):
            if field_name in self.fields:
                self.fields[field_name].widget = forms.Textarea(attrs={'rows': 6})

        for field_name in ASSET_REQUIRED_TEXT_FIELDS:
            if field_name in self.fields:
                self.fields[field_name].widget = forms.Textarea(attrs={'rows': 3})

        if self.instance._state.adding:
            for field_name in ('transaction_hash', 'envelope_xdr', 'discord_username'):
                if field_name in self.fields:
                    self.fields[field_name].required = True

    class Meta:
        model = Proposal
        fields = forms.ALL_FIELDS

    def _is_asset_manager(self) -> bool:
        request_user = getattr(self, 'request_user', None)
        return bool(
            request_user
            and request_user.is_authenticated
            and not request_user.is_superuser
            and request_user.has_perm('governance.manage_asset_proposals')
        )

    def clean(self):
        cleaned_data = super().clean()
        proposal_type = cleaned_data.get('proposal_type') or self.instance.proposal_type or Proposal.PROPOSAL_TYPE_GENERAL

        if self._is_asset_manager() and not Proposal.is_asset_proposal_type(proposal_type):
            raise ValidationError({'proposal_type': 'Managers can manage only asset proposals.'})

        if proposal_type == Proposal.PROPOSAL_TYPE_GENERAL:
            self._validate_general_payload(cleaned_data)
        elif Proposal.is_asset_proposal_type(proposal_type):
            self._validate_asset_payload(cleaned_data)
        else:
            raise ValidationError({'proposal_type': 'Unsupported proposal_type value.'})

        if self.instance._state.adding:
            self.instance.draft = True
            self.instance.action = Proposal.TO_CREATE

        if cleaned_data.get('envelope_xdr'):
            payment_status = check_transaction_xdr(cleaned_data, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
            self.instance.payment_status = payment_status
            if self.instance._state.adding and payment_status != Proposal.FINE:
                self.instance.hide = True

        return cleaned_data

    def _validate_general_payload(self, cleaned_data):
        errors = {}
        for field_name in ASSET_FIELDS:
            if not _value_is_blank(cleaned_data.get(field_name)):
                errors[field_name] = 'General proposal does not support asset fields.'
        if errors:
            raise ValidationError(errors)

    def _validate_asset_payload(self, cleaned_data):
        errors = {}
        for field_name in ASSET_REQUIRED_TEXT_FIELDS:
            if _value_is_blank(cleaned_data.get(field_name)):
                errors[field_name] = 'This field is required for asset proposal.'
        if errors:
            raise ValidationError(errors)

        try:
            validate_asset_payload(
                asset_code=cleaned_data.get('asset_code'),
                asset_issuer=cleaned_data.get('asset_issuer'),
                asset_contract_address=cleaned_data.get('asset_contract_address'),
                require_onchain_verification=False,
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
