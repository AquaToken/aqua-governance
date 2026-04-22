from django.conf import settings
from stellar_sdk import SorobanServer


def get_soroban_transaction(tx_hash: str):
    server = SorobanServer(_get_required_setting('SOROBAN_RPC_URL'))
    return server.get_transaction(tx_hash)


def _get_required_setting(name: str) -> str:
    value = getattr(settings, name, '')
    if not value:
        raise RuntimeError(f'Required setting `{name}` is not configured.')
    return value
