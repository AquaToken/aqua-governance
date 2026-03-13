from stellar_sdk import Address


def normalize_asset_addresses(args: list[str]) -> list[str]:
    if not isinstance(args, list):
        raise ValueError("onchain_action_args must be an array of asset addresses.")
    if not args:
        raise ValueError("onchain_action_args must contain at least one asset address.")

    normalized_assets: list[str] = []
    for idx, raw_value in enumerate(args):
        value = str(raw_value).strip()
        if not value:
            raise ValueError(f"onchain_action_args[{idx}] contains an empty asset address.")

        try:
            Address(value)
        except Exception as exc:
            raise ValueError(
                f"onchain_action_args[{idx}] is not a valid Soroban address: {value}",
            ) from exc

        normalized_assets.append(value)

    return normalized_assets
