from datetime import timedelta

from dateutil.parser import parse as date_parse

from aqua_governance.governance.models import LogVote, Proposal


def parse_balance_info(claimable_balance: dict, proposal: Proposal, vote_choice: str):

    balance_id = claimable_balance['id']
    asset_code = claimable_balance['asset'].split(':')[0]
    amount = claimable_balance['amount']
    sponsor = claimable_balance['sponsor']
    last_modified_time = claimable_balance['last_modified_time']
    transaction_link = claimable_balance['_links']['transactions']['href'].replace('{?cursor,limit,order}', '')

    claimants = claimable_balance['claimants']

    time_list = []
    for claimant in claimants:
        abs_before = claimant.get('predicate', None).get('not', None).get('abs_before', None)
        if abs_before and date_parse(abs_before) >= proposal.end_at - timedelta(seconds=1) + 2 * (date_parse(last_modified_time) - timedelta(minutes=15) - proposal.start_at):
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
    )
