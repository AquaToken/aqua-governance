import logging
from typing import Optional

from django.conf import settings
from stellar_sdk import Keypair, SorobanServer, TransactionBuilder, scval
from stellar_sdk.exceptions import PrepareTransactionException
from stellar_sdk.soroban_rpc import SendTransactionStatus

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.asset_payload import normalize_asset_addresses


logger = logging.getLogger(__name__)


class OnchainReadError(RuntimeError):
    """Raised when reading state from asset-registry via Soroban RPC fails."""


def read_onchain_whitelist_state(asset_contract_address: str) -> bool:
    """Read current whitelist state for an asset from the asset-registry contract.

    Invokes the contract's `status(asset: Address) -> bool` view function via
    `SorobanServer.simulate_transaction` — no signing, no fees. Returns the bool
    the contract holds for this asset.

    Notes on contract semantics (see `aquarius-voting-system/contracts/asset-registry`):
      - `status` returns `false` for assets never registered AND for assets that
        were registered then REMOVED (both stored as `Some(false)` or absent).
      - The view never throws; absent entries default to `false`.

    Raises `OnchainReadError` on any RPC / simulation / parse failure. Callers
    (admin reconciliation action) treat this as fail-closed — surface the error
    to the operator rather than guessing state.
    """
    contract_id = _get_required_setting('ONCHAIN_ASSET_REGISTRY_CONTRACT_ID')
    rpc_url = _get_required_setting('SOROBAN_RPC_URL')
    # We need ANY source account for simulate_transaction — manager pubkey is
    # convenient and already-configured. No signing happens here; the simulate
    # endpoint does not require valid auth, only a syntactically valid tx.
    manager_secret = _get_required_setting('ONCHAIN_ASSET_REGISTRY_MANAGER_SECRET')
    manager_address = Keypair.from_secret(manager_secret).public_key

    # Normalize / validate the address up front — the helper raises ValueError
    # on bad input, which we translate to OnchainReadError for the caller.
    try:
        normalized = normalize_asset_addresses([asset_contract_address])[0]
    except ValueError as exc:
        raise OnchainReadError(f'Invalid asset address: {exc}') from exc

    server = SorobanServer(rpc_url)
    try:
        source_account = server.load_account(manager_address)
        transaction = (
            TransactionBuilder(
                source_account,
                settings.NETWORK_PASSPHRASE,
                base_fee=settings.ONCHAIN_SOROBAN_BASE_FEE,
            )
            .set_timeout(settings.ONCHAIN_SOROBAN_TIMEOUT)
            .append_invoke_contract_function_op(
                contract_id=contract_id,
                function_name='status',
                parameters=[scval.to_address(normalized)],
            )
            .build()
        )
        sim_response = server.simulate_transaction(transaction)
    except Exception as exc:
        raise OnchainReadError(
            f'Soroban simulate_transaction failed for status({normalized}): {exc}',
        ) from exc

    sim_error = getattr(sim_response, 'error', None)
    if sim_error:
        raise OnchainReadError(
            f'asset-registry.status({normalized}) simulation returned error: {sim_error}',
        )

    results = getattr(sim_response, 'results', None) or []
    if not results:
        raise OnchainReadError(
            f'asset-registry.status({normalized}) returned no results in simulation response',
        )

    # results[0].xdr is a base64-encoded ScVal; `scval.from_bool` accepts that
    # form directly and raises ValueError on non-bool ScVal types.
    try:
        return scval.from_bool(results[0].xdr)
    except Exception as exc:
        raise OnchainReadError(
            f'Failed to parse ScVal bool from status({normalized}) result: {exc}',
        ) from exc


def execute_asset_registry_action(proposal: Proposal, args: list[str], allowed: bool) -> Optional[str]:
    contract_id = _get_required_setting("ONCHAIN_ASSET_REGISTRY_CONTRACT_ID")
    manager_secret = _get_required_setting("ONCHAIN_ASSET_REGISTRY_MANAGER_SECRET")
    rpc_url = _get_required_setting("SOROBAN_RPC_URL")

    manager_keypair = Keypair.from_secret(manager_secret)
    manager_address = manager_keypair.public_key
    assets = normalize_asset_addresses(args)
    meta_hash = _build_empty_meta_hash()

    server = SorobanServer(rpc_url)
    actions = [
        scval.to_struct({
            "asset": scval.to_address(asset_address),
            "allowed": scval.to_bool(allowed),
        })
        for asset_address in assets
    ]
    parameters = [
        scval.to_address(manager_address),
        scval.to_uint64(proposal.id),
        scval.to_vec(actions),
        scval.to_bytes(meta_hash),
    ]
    tx_hash = _send_execute_proposal_transaction(
        server=server,
        manager_address=manager_address,
        manager_keypair=manager_keypair,
        contract_id=contract_id,
        parameters=parameters,
        proposal_id=proposal.id,
    )
    return tx_hash


def submit_reconciliation_action(
    *,
    source_proposal_id: int,
    asset_contract_address: str,
    allowed: bool,
) -> str:
    """Push a single-asset corrective tx to asset-registry to align onchain state with DB.

    Used by the AssetTokenAdmin `sync_with_onchain_contract` action when
    `read_onchain_whitelist_state` reports a divergence between the contract
    and `AssetToken.whitelisted`.

    `source_proposal_id` is taken directly from the latest VOTED+SUCCESS
    proposal for this token — it provides the governance authority anchor that
    set the current DB state. Reusing the id means the onchain
    `proposal_executed` event will emit again under the same governance id;
    this is accepted as a known trade-off for keeping reconciliation tx
    obviously linked to the source decision (see codex-space workdoc
    `onchain-reconciliation-mvp-2026-05-22.md`).

    Raises `RuntimeError` on Soroban submission failure (same semantics as
    `execute_asset_registry_action`). The admin action wraps the call and
    surfaces the error to the operator.
    """
    contract_id = _get_required_setting('ONCHAIN_ASSET_REGISTRY_CONTRACT_ID')
    manager_secret = _get_required_setting('ONCHAIN_ASSET_REGISTRY_MANAGER_SECRET')
    rpc_url = _get_required_setting('SOROBAN_RPC_URL')

    manager_keypair = Keypair.from_secret(manager_secret)
    manager_address = manager_keypair.public_key
    normalized_assets = normalize_asset_addresses([asset_contract_address])
    meta_hash = _build_empty_meta_hash()

    server = SorobanServer(rpc_url)
    actions = [
        scval.to_struct({
            'asset': scval.to_address(normalized_assets[0]),
            'allowed': scval.to_bool(allowed),
        }),
    ]
    parameters = [
        scval.to_address(manager_address),
        scval.to_uint64(source_proposal_id),
        scval.to_vec(actions),
        scval.to_bytes(meta_hash),
    ]
    logger.info(
        'Submitting reconciliation tx to asset-registry: asset=%s allowed=%s source_proposal_id=%s',
        normalized_assets[0], allowed, source_proposal_id,
    )
    return _send_execute_proposal_transaction(
        server=server,
        manager_address=manager_address,
        manager_keypair=manager_keypair,
        contract_id=contract_id,
        parameters=parameters,
        proposal_id=source_proposal_id,
    )


def _send_execute_proposal_transaction(
    server: SorobanServer,
    manager_address: str,
    manager_keypair: Keypair,
    contract_id: str,
    parameters: list,
    proposal_id: int,
) -> str:
    send_result = None

    for attempt in range(2):
        prepared_transaction = _prepare_execute_proposal_transaction(
            server=server,
            manager_address=manager_address,
            manager_keypair=manager_keypair,
            contract_id=contract_id,
            parameters=parameters,
        )
        send_result = server.send_transaction(prepared_transaction)
        if send_result.status != SendTransactionStatus.ERROR:
            return send_result.hash

        if attempt == 0:
            logger.warning(
                "Soroban send failed for proposal %s on first attempt; retrying with refreshed sequence. "
                "hash=%s error_xdr=%s",
                proposal_id,
                send_result.hash,
                send_result.error_result_xdr,
            )

    raise RuntimeError(
        f"Soroban send failed after retry. hash={send_result.hash} error_xdr={send_result.error_result_xdr}",
    )


def _prepare_execute_proposal_transaction(
    server: SorobanServer,
    manager_address: str,
    manager_keypair: Keypair,
    contract_id: str,
    parameters: list,
):
    source_account = server.load_account(manager_address)
    transaction = (
        TransactionBuilder(
            source_account,
            settings.NETWORK_PASSPHRASE,
            base_fee=settings.ONCHAIN_SOROBAN_BASE_FEE,
        )
        .set_timeout(settings.ONCHAIN_SOROBAN_TIMEOUT)
        .append_invoke_contract_function_op(
            contract_id=contract_id,
            function_name="execute_proposal",
            parameters=parameters,
        )
        .build()
    )

    try:
        prepared_transaction = server.prepare_transaction(transaction)
    except PrepareTransactionException as exc:
        simulation_error = getattr(exc.simulate_transaction_response, "error", None)
        raise RuntimeError(
            f"Failed to prepare onchain transaction. simulation_error={simulation_error}",
        ) from exc

    prepared_transaction.sign(manager_keypair)
    return prepared_transaction


def _build_empty_meta_hash() -> bytes:
    # Contract expects BytesN<32>; use all-zero payload until a canonical meta hash format is defined.
    return bytes(32)


def _get_required_setting(name: str) -> str:
    value = getattr(settings, name, "")
    if not value:
        raise RuntimeError(f"Required setting `{name}` is not configured.")
    return value
