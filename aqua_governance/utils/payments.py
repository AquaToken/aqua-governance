import base64
import hashlib

from django.conf import settings

from stellar_sdk import Server, Payment, HashMemo, TransactionEnvelope

from aqua_governance.governance.models import Proposal
from aqua_governance.utils.requests import load_all_records


def check_payment(tx_hash, payment_amount=settings.PROPOSAL_COST):
    try:
        horizon_server = Server(settings.HORIZON_URL)
        for operation in load_all_records(horizon_server.operations().for_transaction(tx_hash)):
            operation_type = operation.get('type', None)

            if not operation_type or operation_type != 'payment':
                continue

            if operation['asset_code'] == settings.AQUA_ASSET_CODE and operation['asset_issuer'] == settings.AQUA_ASSET_ISSUER and\
                    operation['to'] == settings.AQUA_ASSET_ISSUER and float(operation['amount']) >= payment_amount:
                return True
    except Exception as ex:
        print(ex)

    return False


def check_xdr_payment(transaction_envelope, payment_amount=settings.PROPOSAL_COST):
    for operation in transaction_envelope.transaction.operations:
        if not isinstance(operation, Payment):
            continue
        if operation.asset.code == settings.AQUA_ASSET_CODE and operation.asset.issuer == settings.AQUA_ASSET_ISSUER and \
            operation.destination.account_id == settings.AQUA_ASSET_ISSUER and float(operation.amount) >= payment_amount:
            return True

    return False


def check_proposal_status(instance, payment_amount=settings.PROPOSAL_COST):
    horizon_server = Server(settings.HORIZON_URL)
    try:
        transaction_info = horizon_server.transactions().transaction(instance.transaction_hash).call()
    except Exception:
        return Proposal.HORIZON_ERROR

    proposal = Proposal.objects.get(transaction_hash=instance.transaction_hash)
    if not transaction_info.get('successful', None):
        return Proposal.FAILED_TRANSACTION
    if not check_payment(instance.transaction_hash, payment_amount):
        return Proposal.INVALID_PAYMENT

    memo = transaction_info.get('memo', None)
    if not memo:
        return Proposal.BAD_MEMO

    text_hash = hashlib.sha256(proposal.text.html.encode('utf-8')).hexdigest()

    if not base64.b64encode(HashMemo(text_hash).memo_hash).decode() == memo:
        return Proposal.BAD_MEMO

    return Proposal.FINE


def check_transaction_xdr(data, payment_amount=settings.PROPOSAL_COST):
    envelope_xdr = data.get('envelope_xdr', None)
    try:
        transaction_envelope = TransactionEnvelope.from_xdr(envelope_xdr, settings.NETWORK_PASSPHRASE)
    except Exception:
        return Proposal.HORIZON_ERROR

    if not check_xdr_payment(transaction_envelope, payment_amount):
        return Proposal.INVALID_PAYMENT

    memo = transaction_envelope.transaction.memo
    text_hash = hashlib.sha256(data['text'].html.encode('utf-8')).hexdigest()

    if not isinstance(memo, HashMemo) or not HashMemo(text_hash).memo_hash == memo.memo_hash:
        return Proposal.BAD_MEMO

    return Proposal.FINE
