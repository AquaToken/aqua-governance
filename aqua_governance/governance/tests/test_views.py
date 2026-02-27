from django.test import TestCase
from rest_framework.test import APIClient

from aqua_governance.governance.models import AssetRecord


class AssetRegistryViewTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_list_asset_registry_orders_by_updated_ledger_desc(self):
        AssetRecord.objects.create(asset_address='G' + 'A' * 55, updated_ledger=10)
        AssetRecord.objects.create(asset_address='G' + 'B' * 55, updated_ledger=20)

        response = self.client.get('/api/asset-registry/')

        self.assertEqual(response.data['results'][0]['updated_ledger'], 20)

    def test_list_asset_registry_filters_by_status(self):
        AssetRecord.objects.create(asset_address='G' + 'C' * 55, status=AssetRecord.ALLOWED)
        AssetRecord.objects.create(asset_address='G' + 'D' * 55, status=AssetRecord.DENIED)

        response = self.client.get('/api/asset-registry/?status=allowed')

        self.assertEqual(response.data['count'], 1)

    def test_retrieve_asset_registry_by_asset_address(self):
        asset_address = 'G' + 'E' * 55
        AssetRecord.objects.create(asset_address=asset_address)

        response = self.client.get('/api/asset-registry/%s/' % asset_address)

        self.assertEqual(response.status_code, 200)
