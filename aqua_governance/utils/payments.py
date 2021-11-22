from django.conf import settings

from stellar_sdk import Server

from aqua_governance.utils.requests import load_all_records


PROPOSAL_COST = 1
AQUA_ASSET_CODE = 'AQUA'
AQUA_ASSET_ISSUER = 'GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA'


def check_payment(tx_hash):
    horizon_server = Server(settings.HORIZON_URL)
    for operation in load_all_records(horizon_server.operations().for_transaction(tx_hash)):
        operation_type = operation.get('type', None)

        if not operation_type or operation_type != 'payment':
            continue

        if operation['asset_code'] == AQUA_ASSET_CODE and operation['asset_issuer'] == AQUA_ASSET_ISSUER and\
                operation['to'] == AQUA_ASSET_ISSUER and float(operation['amount']) >= PROPOSAL_COST:
            return True

    return False
