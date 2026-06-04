"""Tests de cálculo de stock POS / inventario por sucursal."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from products.inventory_stock import (
    _scale_variants_to_cap,
    inventory_disponible_sucursal,
    variant_stock_breakdown_sucursal,
)
from products.models import Inventory, Product, ProductVariant
from vendors.models import Almacen, Sucursal, Vendor


class InventoryStockSucursalTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email='stock@test.com',
            password='test-pass-123',
        )
        self.vendor = Vendor.objects.create(
            user=user,
            nombre_tienda='Test',
            slug='test-stock',
        )
        self.sucursal = Sucursal.objects.create(
            vendor=self.vendor,
            nombre='Central',
            activa=True,
        )
        self.almacen = Almacen.objects.create(
            sucursal=self.sucursal,
            nombre='Depósito',
            activo=True,
        )
        self.product = Product.objects.create(
            vendor=self.vendor,
            name='Camisa',
            price=100,
            is_active=True,
        )

    def test_sin_variantes_usa_inventario_sucursal(self):
        Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=12,
            reserved_quantity=2,
            is_active=True,
        )
        disp = inventory_disponible_sucursal(self.product.id, self.sucursal.id)
        self.assertEqual(disp, 10)
        breakdown = variant_stock_breakdown_sucursal(self.product.id, self.sucursal.id)
        self.assertEqual(breakdown['disponible_total'], 10)

    def test_legacy_sin_almacen_cuenta_en_sucursal(self):
        Inventory.objects.create(
            product=self.product,
            almacen=None,
            quantity=8,
            reserved_quantity=0,
            is_active=True,
        )
        disp = inventory_disponible_sucursal(self.product.id, self.sucursal.id)
        self.assertEqual(disp, 8)

    def test_una_variante_con_fisico_y_stock_extra_cero(self):
        ProductVariant.objects.create(
            product=self.product,
            talla='M',
            color='Azul',
            stock_extra=0,
            is_active=True,
        )
        Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=5,
            is_active=True,
        )
        breakdown = variant_stock_breakdown_sucursal(self.product.id, self.sucursal.id)
        self.assertEqual(breakdown['disponible_total'], 5)
        self.assertEqual(breakdown['variantes'][0]['disponible'], 5)

    def test_variantes_escaladas_al_inventario_sucursal(self):
        ProductVariant.objects.create(
            product=self.product, talla='S', stock_extra=10, is_active=True,
        )
        ProductVariant.objects.create(
            product=self.product, talla='M', stock_extra=10, is_active=True,
        )
        Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=6,
            is_active=True,
        )
        breakdown = variant_stock_breakdown_sucursal(self.product.id, self.sucursal.id)
        self.assertEqual(breakdown['disponible_total'], 6)
        self.assertEqual(
            sum(v['disponible'] for v in breakdown['variantes']),
            6,
        )


class ScaleVariantsTests(TestCase):
    def test_scale_proportional(self):
        variantes = [
            {'id': 1, 'disponible': 10},
            {'id': 2, 'disponible': 10},
        ]
        scaled, total = _scale_variants_to_cap(variantes, 6)
        self.assertEqual(total, 6)
        self.assertEqual(sum(v['disponible'] for v in scaled), 6)
