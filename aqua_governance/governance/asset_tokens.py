from django.conf import settings
from stellar_sdk import Asset

from aqua_governance.governance.models import AssetToken, Proposal


def normalize_asset_value(value):
    """Strip whitespace and return ``None`` for blank/``None`` values."""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def derive_asset_contract_address(*, asset_code, asset_issuer, asset_contract_address):
    """
    Derive the Soroban contract address from a classic asset pair (code +
    issuer) via ``stellar_sdk.Asset``.

    * If both classic and an explicit contract address are provided, validate
      they match.
    * If only the classic pair is provided, derive and return the contract
      address.
    * If only the contract address is provided, return it as-is.
    * If neither is provided, raise ``ValueError``.
    """
    normalized_code = normalize_asset_value(asset_code)
    normalized_issuer = normalize_asset_value(asset_issuer)
    normalized_contract = normalize_asset_value(asset_contract_address)

    has_classic = bool(normalized_code) and bool(normalized_issuer)
    has_contract = bool(normalized_contract)

    if bool(normalized_code) != bool(normalized_issuer):
        raise ValueError("Provide both asset_code and asset_issuer together.")

    derived = None
    if has_classic:
        derived = Asset(normalized_code, normalized_issuer).contract_id(
            settings.NETWORK_PASSPHRASE
        )

    if has_contract:
        if derived and derived != normalized_contract:
            raise ValueError(
                "asset_contract_address does not match asset_code + asset_issuer."
            )
        return normalized_contract

    if derived:
        return derived

    raise ValueError("Provide asset_code + asset_issuer, or asset_contract_address.")


def upsert_asset_token_from_proposal(proposal, save=True):
    """
    Create or update the :class:`AssetToken` for an asset proposal.

    * Derives the contract address (validating consistency).
    * Fills ``proposal.asset_contract_address`` if it is still blank.
    * Links ``proposal.asset_token`` to the token.
    * Persists changed fields on the proposal when ``save=True``.

    Returns the :class:`AssetToken`, or ``None`` for non-asset proposals.
    """
    if not proposal.is_asset_proposal:
        return None

    asset_code = normalize_asset_value(proposal.asset_code)
    asset_issuer = normalize_asset_value(proposal.asset_issuer)
    existing_contract_address = normalize_asset_value(proposal.asset_contract_address)

    contract_address = derive_asset_contract_address(
        asset_code=asset_code,
        asset_issuer=asset_issuer,
        asset_contract_address=existing_contract_address,
    )

    if not existing_contract_address:
        proposal.asset_contract_address = contract_address

    # Create or update AssetToken
    token, created = AssetToken.objects.get_or_create(
        contract_address=contract_address,
        defaults={
            "classic_code": asset_code,
            "classic_issuer": asset_issuer,
        },
    )

    if not created:
        update_fields = []
        if asset_code and not token.classic_code:
            token.classic_code = asset_code
            update_fields.append("classic_code")
        if asset_issuer and not token.classic_issuer:
            token.classic_issuer = asset_issuer
            update_fields.append("classic_issuer")
        if update_fields:
            token.save(update_fields=update_fields)

    # Link proposal to token
    proposal.asset_token = token

    if save:
        proposal_updates = {}
        if not existing_contract_address:
            proposal_updates["asset_contract_address"] = contract_address
        proposal_updates["asset_token_id"] = token.pk
        type(proposal).objects.filter(pk=proposal.pk).update(**proposal_updates)

    return token
