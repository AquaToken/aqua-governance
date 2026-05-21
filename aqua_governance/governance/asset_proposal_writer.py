"""Source-of-truth writer for AssetToken + AssetProposalPayload.

After Stage 2 single-shot, `Proposal` no longer carries `asset_*` columns.
All asset payload flows through this module: serializers / admin form / migration backfill
hand it a `asset_data` dict and a Proposal instance, and it persists the canonical
records atomically.
"""
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from stellar_sdk import Asset

logger = logging.getLogger(__name__)


NARRATIVE_FIELD_MAP = (
    # (asset_data key, AssetProposalPayload attribute)
    ('asset_issuer_information', 'issuer_information'),
    ('asset_token_description', 'token_description'),
    ('asset_holder_distribution', 'holder_distribution'),
    ('asset_liquidity', 'liquidity'),
    ('asset_trading_volume', 'trading_volume'),
    ('asset_audit_info', 'audit_info'),
    ('asset_stellar_flags', 'stellar_flags'),
    ('asset_related_projects', 'related_projects'),
    ('asset_community_references', 'community_references'),
    ('asset_aquarius_traction', 'aquarius_traction'),
    ('asset_issuer_commitments', 'issuer_commitments'),
)


def _assert_network_passphrase():
    """Fail loud if NETWORK_PASSPHRASE is missing.

    Without it `Asset.contract_id(passphrase)` cannot compute the canonical contract
    address, which is the PK of AssetToken. Misconfigured environment would create
    AssetToken rows with wrong PK; refuse to operate in that state.
    """
    passphrase = getattr(settings, 'NETWORK_PASSPHRASE', None)
    if not passphrase:
        raise ImproperlyConfigured(
            'NETWORK_PASSPHRASE is required for asset payload writer; '
            'set it in Django settings before creating asset proposals.',
        )
    return passphrase


def _canonical_contract_address(asset_data):
    """Compute canonical AssetToken PK from asset_data dict.

    Prefers derived (Asset.contract_id) over explicit when classic pair is present;
    explicit is only used when classic pair is missing (pure Soroban token).
    Returns None when neither classic pair nor explicit contract_address is provided.
    """
    passphrase = _assert_network_passphrase()

    explicit = (asset_data.get('asset_contract_address') or '').strip()
    code = (asset_data.get('asset_code') or '').strip()
    issuer = (asset_data.get('asset_issuer') or '').strip()

    derived = None
    if code and issuer:
        try:
            derived = Asset(code, issuer).contract_id(passphrase)
        except Exception:
            logger.exception(
                'Failed to derive contract address from classic pair (code=%r issuer=%r); '
                'falling back to explicit value if provided.', code, issuer,
            )
            derived = None

    if explicit and derived and explicit != derived:
        logger.warning(
            'Asset data: explicit contract_address %s differs from derived %s; '
            'using derived as canonical.', explicit, derived,
        )
        return derived

    return derived or explicit or None


def assert_asset_payload_immutable(proposal, asset_data):
    """Guard against changing the AssetToken FK of an existing AssetProposalPayload.

    The model removed `Proposal._validate_execution_source_fields_immutable` for
    `asset_code/issuer/contract_address` (those columns no longer exist). The
    equivalent invariant — execution source token does not change after creation —
    now lives here at the writer boundary.

    Raises ValidationError if proposal already has an asset_payload whose canonical
    contract_address differs from the canonical derived from incoming asset_data.
    """
    existing_payload = getattr(proposal, 'asset_payload', None)
    if existing_payload is None:
        return

    incoming = _canonical_contract_address(asset_data)
    if not incoming:
        return  # nothing to compare; upsert will be a no-op anyway

    existing = existing_payload.asset_token_id
    if existing != incoming:
        raise ValidationError(
            'Asset token for proposal {} is immutable: existing {} vs incoming {}'.format(
                proposal.id, existing, incoming,
            ),
        )


@transaction.atomic
def upsert_asset_records(proposal, asset_data):
    """Persist AssetToken + AssetProposalPayload from validated asset_data.

    Idempotent — re-running with identical payload is a no-op.
    Atomic — token + payload + classic metadata enrichment all happen together,
    or none of them happen.

    Args:
        proposal: a saved Proposal instance.
        asset_data: dict with keys `asset_code`, `asset_issuer`, `asset_contract_address`,
                    plus 11 narrative keys (`asset_issuer_information`, ...).
                    Identifier keys may be empty/None for non-asset proposals (caller
                    typically gates this by Proposal.is_asset_proposal_type already).

    Returns:
        AssetProposalPayload instance, or None if proposal is not asset-type.
    """
    from aqua_governance.governance.models import AssetProposalPayload, AssetToken, Proposal

    if not Proposal.is_asset_proposal_type(proposal.proposal_type):
        return None

    contract_address = _canonical_contract_address(asset_data)
    if not contract_address:
        return None

    assert_asset_payload_immutable(proposal, asset_data)

    incoming_code = (asset_data.get('asset_code') or '').strip() or None
    incoming_issuer = (asset_data.get('asset_issuer') or '').strip() or None

    token, _created = AssetToken.objects.get_or_create(
        contract_address=contract_address,
        defaults={
            'classic_code': incoming_code,
            'classic_issuer': incoming_issuer,
        },
    )

    token_updates = {}
    for field, incoming in (('classic_code', incoming_code), ('classic_issuer', incoming_issuer)):
        if incoming is None:
            continue
        existing = getattr(token, field) or None
        if existing is None:
            token_updates[field] = incoming
        elif existing != incoming:
            logger.warning(
                'AssetToken %s %s mismatch on Proposal %s: existing=%r incoming=%r; '
                'keeping existing value.',
                contract_address, field, proposal.id, existing, incoming,
            )
    if token_updates:
        for field, value in token_updates.items():
            setattr(token, field, value)
        token.save(update_fields=list(token_updates.keys()) + ['updated_at'])

    payload_fields = {
        payload_attr: (asset_data.get(source_key) or '')
        for source_key, payload_attr in NARRATIVE_FIELD_MAP
    }

    payload, _ = AssetProposalPayload.objects.update_or_create(
        proposal=proposal,
        defaults={'asset_token': token, **payload_fields},
    )
    return payload


def asset_data_from_proposal(proposal):
    """Extract asset_data dict from an existing Proposal+AssetProposalPayload.

    Used by forms / admin / serializers to populate initial values when editing an
    existing asset proposal (after model columns are gone, the canonical source is
    `proposal.asset_payload.asset_token` + narrative fields).
    """
    payload = getattr(proposal, 'asset_payload', None)
    if payload is None:
        return {key: '' for key, _ in NARRATIVE_FIELD_MAP} | {
            'asset_code': '', 'asset_issuer': '', 'asset_contract_address': '',
        }
    token = payload.asset_token
    data = {
        'asset_code': token.classic_code or '',
        'asset_issuer': token.classic_issuer or '',
        'asset_contract_address': token.contract_address or '',
    }
    for source_key, payload_attr in NARRATIVE_FIELD_MAP:
        data[source_key] = getattr(payload, payload_attr, '') or ''
    return data
