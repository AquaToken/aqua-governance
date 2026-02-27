import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from stellar_sdk import Account, Keypair, SorobanServer, TransactionBuilder, scval, xdr
from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus


MAX_WAIT_SECONDS = 30
POLL_INTERVAL = 2

STATUS_CODE_TO_LABEL = {
    0: 'unknown',
    1: 'allowed',
    2: 'denied',
}


def _get_server():
    return SorobanServer(settings.SOROBAN_RPC_URL)


def _get_contract_address():
    contract_address = settings.ASSET_REGISTRY_CONTRACT_ADDRESS
    if not contract_address:
        raise ImproperlyConfigured('ASSET_REGISTRY_CONTRACT_ADDRESS is not configured')
    return contract_address


def _get_operator_secret_key(required=True):
    secret_key = settings.REGISTRY_OPERATOR_SECRET_KEY
    if required and not secret_key:
        raise ImproperlyConfigured('REGISTRY_OPERATOR_SECRET_KEY is not configured')
    return secret_key


def _build_invoke_transaction(source_account, function_name, parameters):
    return (
        TransactionBuilder(
            source_account=source_account,
            network_passphrase=settings.NETWORK_PASSPHRASE,
        )
        .append_invoke_contract_function_op(
            contract_id=_get_contract_address(),
            function_name=function_name,
            parameters=parameters,
        )
        .set_timeout(MAX_WAIT_SECONDS)
        .build()
    )


def _wait_for_transaction(server, tx_hash):
    deadline_at = time.time() + MAX_WAIT_SECONDS
    last_status = None

    while time.time() < deadline_at:
        transaction = server.get_transaction(tx_hash)
        last_status = transaction.status

        if transaction.status == GetTransactionStatus.SUCCESS:
            return

        if transaction.status == GetTransactionStatus.FAILED:
            raise Exception(f'Soroban transaction failed: {tx_hash}')

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f'Transaction {tx_hash} was not finalized. Last status: {last_status}')


def set_asset_status(asset_address, status_code, proposal_id, meta_hash):
    operator_keypair = Keypair.from_secret(_get_operator_secret_key(required=True))
    server = _get_server()
    source_account = server.load_account(operator_keypair.public_key)

    meta_hash_bytes = bytes.fromhex(meta_hash)
    if len(meta_hash_bytes) != 32:
        raise ValueError('meta_hash must be a 32-byte SHA256 hex string')

    tx = _build_invoke_transaction(
        source_account=source_account,
        function_name='set_status',
        parameters=[
            scval.to_address(operator_keypair.public_key),
            scval.to_address(asset_address),
            scval.to_uint32(status_code),
            scval.to_uint64(proposal_id),
            scval.to_bytes(meta_hash_bytes),
        ],
    )

    prepared_tx = server.prepare_transaction(tx)
    prepared_tx.sign(operator_keypair)
    response = server.send_transaction(prepared_tx)

    if response.status == SendTransactionStatus.ERROR:
        raise Exception(f'Failed to submit Soroban transaction: {response.error_result_xdr}')
    if response.status == SendTransactionStatus.TRY_AGAIN_LATER:
        raise Exception('Soroban RPC asked to retry transaction submission later')

    _wait_for_transaction(server, response.hash)
    return response.hash


def _get_simulation_source_account(server):
    operator_secret = _get_operator_secret_key(required=False)
    if operator_secret:
        operator_keypair = Keypair.from_secret(operator_secret)
        try:
            return server.load_account(operator_keypair.public_key)
        except Exception:
            return Account(operator_keypair.public_key, sequence=0)

    random_keypair = Keypair.random()
    return Account(random_keypair.public_key, sequence=0)


def _parse_registry_result(result_sc_val):
    parsed_items = []
    raw_items = scval.from_vec(result_sc_val)

    for raw_item in raw_items:
        pair = scval.from_vec(raw_item)
        if len(pair) != 2:
            raise Exception('Invalid registry tuple shape in Soroban response')

        asset_address = scval.from_address(pair[0]).address
        record = scval.to_native(pair[1])
        if not isinstance(record, dict):
            raise Exception('Invalid asset record shape in Soroban response')

        status_code = int(record.get('status', 0) or 0)
        meta_hash_value = record.get('meta_hash', b'')
        if isinstance(meta_hash_value, bytes):
            meta_hash_hex = meta_hash_value.hex()
        elif meta_hash_value in (None, ''):
            meta_hash_hex = ''
        else:
            meta_hash_hex = str(meta_hash_value)

        parsed_items.append(
            {
                'asset_address': asset_address,
                'status': STATUS_CODE_TO_LABEL.get(status_code, 'unknown'),
                'added_ledger': int(record.get('added_ledger', 0) or 0),
                'updated_ledger': int(record.get('updated_ledger', 0) or 0),
                'last_proposal_id': int(record.get('last_proposal_id', 0) or 0),
                'meta_hash': meta_hash_hex,
            }
        )

    return parsed_items


def fetch_registry_page(offset, limit):
    server = _get_server()
    source_account = _get_simulation_source_account(server)

    tx = _build_invoke_transaction(
        source_account=source_account,
        function_name='list',
        parameters=[
            scval.to_uint32(offset),
            scval.to_uint32(limit),
        ],
    )
    response = server.simulate_transaction(tx)

    if response.error:
        raise Exception(f'Soroban simulate error: {response.error}')
    if not response.results:
        raise Exception('Soroban simulate returned no results')

    result_sc_val = xdr.SCVal.from_xdr(response.results[0].xdr)
    return _parse_registry_result(result_sc_val)
