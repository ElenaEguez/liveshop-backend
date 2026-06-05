"""Tests de stock_service: consolidación de inventario duplicado."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from products.models import Inventory, Product
from products.stock_service import apply_stock_delta, get_or_create_inventory
from vendors.models import Almacen, Sucursal, Vendor


class StockServiceDuplicateInventoryTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email='dup-inv@test.com',
            password='test-pass-123',
        )
        self.vendor = Vendor.objects.create(
            user=user,
            nombre_tienda='Dup Test',
            slug='dup-test',
        )
        self.sucursal = Sucursal.objects.create(
            vendor=self.vendor,
            nombre='Central',
            activa=True,
        )
        self.almacen = Almacen.objects.create(
            sucursal=self.sucursal,
            nombre='GAIA',
            activo=True,
        )
        self.product = Product.objects.create(
            vendor=self.vendor,
            name='CHAMARRAS DE CUERO (NC)',
            price=505,
            is_active=True,
        )

    def test_get_or_create_merges_duplicate_rows(self):
        older = Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=0,
            is_active=True,
        )
        newer = Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=2,
            is_active=True,
        )

        result = get_or_create_inventory(
            self.product, self.almacen, lock=True,
        )

        self.assertEqual(result.pk, older.pk)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.quantity, 2)
        self.assertTrue(older.is_active)
        self.assertEqual(newer.quantity, 0)
        self.assertFalse(newer.is_active)

    def test_apply_stock_delta_with_duplicates_increments_primary(self):
        older = Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=0,
            is_active=True,
        )
        Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=2,
            is_active=True,
        )

        apply_stock_delta(
            product=self.product,
            almacen=self.almacen,
            delta=1,
            motivo='compra',
            documento_ref='OC-TEST',
        )

        older.refresh_from_db()
        self.assertEqual(older.quantity, 3)
