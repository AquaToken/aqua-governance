from typing import Callable, Optional

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.onchain_hooks.hooks import add_asset, remove_asset

HookCallable = Callable[[Proposal, list[str]], Optional[str]]

ONCHAIN_HOOKS: dict[str, HookCallable] = {
    Proposal.ONCHAIN_ACTION_ADD_ASSET: add_asset,
    Proposal.ONCHAIN_ACTION_REMOVE_ASSET: remove_asset,
}


def execute_onchain_action(proposal: Proposal) -> Optional[str]:
    hook = ONCHAIN_HOOKS.get(proposal.onchain_action_type)
    if hook is None:
        raise ValueError(f"Unsupported onchain action type: {proposal.onchain_action_type}")

    args = list(proposal.onchain_action_args or [])
    return hook(proposal, args)
