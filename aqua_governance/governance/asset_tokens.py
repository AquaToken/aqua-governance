from aqua_governance.governance.models import Proposal
from aqua_governance.governance.onchain_actions import derive_proposal_onchain_action_args


def canonical_asset_key(proposal) -> str:
    try:
        args = derive_proposal_onchain_action_args(
            asset_code=proposal.asset_code,
            asset_issuer=proposal.asset_issuer,
            asset_contract_address=proposal.asset_contract_address,
        )
        return args[0]
    except ValueError:
        return str((proposal.asset_code, proposal.asset_issuer, proposal.asset_contract_address))


def compute_token_whitelisted(proposals: list) -> bool:
    add_successes = [
        p for p in proposals
        if p.proposal_type == Proposal.PROPOSAL_TYPE_ADD_ASSET
        and p.onchain_execution_status == Proposal.ONCHAIN_EXECUTION_SUCCESS
        and p.proposal_status == Proposal.VOTED
        and p.end_at is not None
    ]
    if not add_successes:
        return False
    latest_add = max(add_successes, key=lambda p: p.end_at)

    remove_successes = [
        p for p in proposals
        if p.proposal_type == Proposal.PROPOSAL_TYPE_REMOVE_ASSET
        and p.onchain_execution_status == Proposal.ONCHAIN_EXECUTION_SUCCESS
        and p.proposal_status == Proposal.VOTED
        and p.end_at is not None
    ]
    if not remove_successes:
        return True
    latest_remove = max(remove_successes, key=lambda p: p.end_at)
    return latest_add.end_at > latest_remove.end_at
