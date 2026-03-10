from typing import Optional

from aqua_governance.governance.models import Proposal


def add_asset(proposal: Proposal, args: list[str]) -> Optional[str]:
    # TODO: Implement actual Stellar whitelist contract invocation for ADD_ASSET.
    raise NotImplementedError(
        "Onchain hook add_asset is not implemented yet. Implement Stellar whitelist contract call.",
    )


def remove_asset(proposal: Proposal, args: list[str]) -> Optional[str]:
    # TODO: Implement actual Stellar whitelist contract invocation for REMOVE_ASSET.
    raise NotImplementedError(
        "Onchain hook remove_asset is not implemented yet. Implement Stellar whitelist contract call.",
    )
