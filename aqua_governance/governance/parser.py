from datetime import timedelta

from dateutil.parser import parse as date_parse
from django.conf import settings
from stellar_sdk import Asset

from aqua_governance.governance.exceptions import ClaimableBalanceParsingError
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.utils.stellar.asset import parse_asset_string


def parse_balance_info(claimable_balance: dict, proposal: Proposal, vote_choice: str, hide=False):
    AQUA_ASSET = Asset(settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_ISSUER)
    ICE_ASSET = Asset(settings.GOVERNANCE_ICE_ASSET_CODE, settings.GOVERNANCE_ICE_ASSET_ISSUER)
    GDICE_ASSET = Asset(settings.GDICE_ASSET_CODE, settings.GDICE_ASSET_ISSUER)

    balance_id = claimable_balance['id']
    asset = parse_asset_string(claimable_balance['asset'])
    asset_code = claimable_balance['asset'].split(':')[0]
    amount = claimable_balance['amount']
    sponsor = claimable_balance['sponsor']
    last_modified_time = claimable_balance['last_modified_time']
    transaction_link = claimable_balance['_links']['transactions']['href'].replace('{?cursor,limit,order}', '')

    claimants = claimable_balance['claimants']

    if asset not in [AQUA_ASSET, ICE_ASSET, GDICE_ASSET]:
        return None

    if last_modified_time is None:
        last_modified_time = str(proposal.created_at)

    time_list = []
    for claimant in claimants:
        abs_before = claimant.get('predicate', None).get('not', None).get('abs_before', None)
        if asset == AQUA_ASSET and abs_before and date_parse(abs_before) >= proposal.end_at - timedelta(seconds=1) + 2 * (date_parse(last_modified_time) - timedelta(minutes=15) - proposal.start_at):
            time_list.append(abs_before)
        elif asset in [ICE_ASSET, GDICE_ASSET] and abs_before and date_parse(abs_before) >= proposal.end_at - timedelta(seconds=1):
            sponsor = claimant['destination']
            time_list.append(abs_before)
    if not time_list:
        return None

    return LogVote(
        claimable_balance_id=balance_id,
        proposal=proposal,
        vote_choice=vote_choice,
        amount=amount,
        account_issuer=sponsor,
        created_at=last_modified_time,
        transaction_link=transaction_link,
        asset_code=asset_code,
        hide=hide,
        claimed=False,
        time_list=time_list
    )


def parse_new_balance_info(claimable_balance: dict, proposal: Proposal, vote_choice: str) -> LogVote:
    AQUA_ASSET = Asset(settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_ISSUER)
    ICE_ASSET = Asset(settings.GOVERNANCE_ICE_ASSET_CODE, settings.GOVERNANCE_ICE_ASSET_ISSUER)
    GDICE_ASSET = Asset(settings.GDICE_ASSET_CODE, settings.GDICE_ASSET_ISSUER)

    balance_id = claimable_balance['id']
    asset = parse_asset_string(claimable_balance['asset'])
    asset_code = claimable_balance['asset'].split(':')[0]
    amount = claimable_balance['amount']
    sponsor = claimable_balance['sponsor']
    last_modified_time = claimable_balance['last_modified_time']
    transaction_link = claimable_balance['_links']['transactions']['href'].replace('{?cursor,limit,order}', '')

    claimants = claimable_balance['claimants']

    if asset not in [AQUA_ASSET, ICE_ASSET, GDICE_ASSET]:
        raise ClaimableBalanceParsingError("Claimable Balance Parsing Error")

    if last_modified_time is None:
        last_modified_time = str(proposal.created_at)

    time_list = []
    for claimant in claimants:
        abs_before = claimant.get('predicate', None).get('not', None).get('abs_before', None)
        if asset == AQUA_ASSET and abs_before and date_parse(abs_before) >= proposal.end_at - timedelta(seconds=1) + 2 * (date_parse(last_modified_time) - timedelta(minutes=15) - proposal.start_at):
            time_list.append(abs_before)
        elif asset in [ICE_ASSET, GDICE_ASSET] and abs_before and date_parse(abs_before) >= proposal.end_at - timedelta(seconds=1):
            sponsor = claimant['destination']
            time_list.append(abs_before)
    if not time_list:
        raise ClaimableBalanceParsingError("Claimable Balance Parsing Error")

    return LogVote(
        claimable_balance_id=balance_id,
        proposal=proposal,
        vote_choice=vote_choice,
        amount=amount,
        account_issuer=sponsor,
        created_at=last_modified_time,
        transaction_link=transaction_link,
        asset_code=asset_code,
        hide=False,
        claimed=False,
        time_list=time_list
    )
