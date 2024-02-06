from stellar_sdk import Asset


def get_asset_string(asset: Asset) -> str:
    if asset.is_native():
        return 'native'

    return f'{asset.code}:{asset.issuer}'


def parse_asset_string(asset_string: str) -> Asset:
    if asset_string == 'native':
        return Asset.native()

    code, issuer = asset_string.split(':')
    return Asset(code, issuer)
