from typing import Optional

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.onchain_hooks.asset_registry import execute_asset_registry_action


def add_asset(proposal: Proposal, args: list[str]) -> Optional[str]:
    return execute_asset_registry_action(proposal=proposal, args=args, allowed=True)


def remove_asset(proposal: Proposal, args: list[str]) -> Optional[str]:
    return execute_asset_registry_action(proposal=proposal, args=args, allowed=False)
