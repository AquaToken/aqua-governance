"""Shared DRF field utilities for legacy flat asset_* JSON shape.

After Stage 2 single-shot, `Proposal.asset_*` columns are gone. Serializers (both
v1 and v2) preserve the legacy flat JSON shape via dotted `source=` mapping through
`asset_payload.asset_token`. This module owns the canonical field class and source
map so v1 / v2 stay in sync without circular imports.
"""
from rest_framework import serializers


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


# Mapping from API input/output key → AssetProposalPayload model attribute or
# AssetToken model attribute (dotted path from Proposal instance).
READ_SOURCE_MAP = {
    'asset_code': 'asset_payload.asset_token.classic_code',
    'asset_issuer': 'asset_payload.asset_token.classic_issuer',
    'asset_contract_address': 'asset_payload.asset_token.contract_address',
    'asset_issuer_information': 'asset_payload.issuer_information',
    'asset_token_description': 'asset_payload.token_description',
    'asset_holder_distribution': 'asset_payload.holder_distribution',
    'asset_liquidity': 'asset_payload.liquidity',
    'asset_trading_volume': 'asset_payload.trading_volume',
    'asset_audit_info': 'asset_payload.audit_info',
    'asset_stellar_flags': 'asset_payload.stellar_flags',
    'asset_related_projects': 'asset_payload.related_projects',
    'asset_community_references': 'asset_payload.community_references',
    'asset_aquarius_traction': 'asset_payload.aquarius_traction',
    'asset_issuer_commitments': 'asset_payload.issuer_commitments',
}


class AssetSourceField(serializers.CharField):
    """Read-only asset payload field with null-safety for non-asset proposals.

    Source resolves the dotted path `asset_payload.<...>`. For GENERAL proposals
    `asset_payload` does not exist (raises RelatedObjectDoesNotExist) — we return None.

    Narrative-fields previously stored as `null=True` on `Proposal.asset_<name>`
    are now stored as `default=''` on `AssetProposalPayload.<name>`. We coalesce
    `''` → `None` in `to_representation` to preserve the legacy wire-format.
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('read_only', True)
        kwargs.setdefault('allow_null', True)
        kwargs.setdefault('required', False)
        super().__init__(*args, **kwargs)

    def get_attribute(self, instance):
        try:
            return super().get_attribute(instance)
        except (AttributeError, KeyError):
            return None
        except serializers.SkipField:
            return None

    def to_representation(self, value):
        if value in (None, ''):
            return None
        return super().to_representation(value)


def asset_read_fields():
    """Build a dict of declared asset_* read fields using AssetSourceField + source=.

    Returned dict is spread into serializer class definitions to keep field
    declarations DRY across List/Detail/Submit/v1 serializers.
    """
    return {
        name: AssetSourceField(source=source)
        for name, source in READ_SOURCE_MAP.items()
    }
