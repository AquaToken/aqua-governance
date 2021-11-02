from aqua_governance.governance.models import LogVote, Proposal


def parse_balance_info(claimable_balance: dict, proposal: Proposal, vote_choice: str) -> LogVote:

    balance_id = claimable_balance['id']
    amount = claimable_balance['amount']
    sponsor = claimable_balance['sponsor']
    last_modified_time = claimable_balance['last_modified_time']

    return LogVote(
        claimable_balance_id=balance_id,
        proposal=proposal,
        vote_choice=vote_choice,
        amount=amount,
        account_issuer=sponsor,
        created_at=last_modified_time,
    )
