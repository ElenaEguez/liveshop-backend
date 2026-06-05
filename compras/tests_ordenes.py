"""Tests de creación de órdenes de compra."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from compras.models import OrdenCompra, Proveedor
from products.models import Product
from vendors.models import Almacen, Sucursal, Vendor


class OrdenCompraCreateTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email='oc-create@test.com',
            password='test-pass-123',
        )
        self.user = user
        self.vendor_a = Vendor.objects.create(
            user=user,
            nombre_tienda='Tienda A',
            slug='tienda-a',
        )
        other = get_user_model().objects.create_user(
            email='other-vendor@test.com',
            password='test-pass-123',
        )
        self.vendor_b = Vendor.objects.create(
            user=other,
            nombre_tienda='Tienda B',
            slug='tienda-b',
        )
        self.sucursal = Sucursal.objects.create(
            vendor=self.vendor_a,
            nombre='Central',
            activa=True,
        )
        self.almacen = Almacen.objects.create(
            sucursal=self.sucursal,
            nombre='GAIA',
            activo=True,
        )
        self.proveedor = Proveedor.objects.create(
            vendor=self.vendor_a,
            nombre='ARGENTINA',
        )
        self.product = Product.objects.create(
            vendor=self.vendor_a,
            name='Producto test',
            price=100,
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.url = '/api/v1/compras/ordenes/'

    def _payload(self):
        return {
            'proveedor': self.proveedor.id,
            'fecha': date.today().isoformat(),
            'factura_compra': '001',
            'almacen': self.almacen.id,
            'estado': 'pendiente',
            'descuento': 0,
            'notas': '',
            'items': [{
                'producto': self.product.id,
                'almacen': self.almacen.id,
                'cantidad': 1,
                'costo_mercaderia': '218.00',
                'flete_unitario': '0',
                'porcentaje_ganancia': 50,
                'precio_unitario': '218.00',
                'precio_venta_es_manual': False,
                'precio_venta_sugerido': '327.00',
            }],
        }

    def test_create_orden_pendiente(self):
        res = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(OrdenCompra.objects.filter(vendor=self.vendor_a).count(), 1)

    def test_numero_unico_por_vendor_aunque_otro_vendor_tenga_mismo(self):
        OrdenCompra.objects.create(
            vendor=self.vendor_b,
            numero='000006',
            fecha=date.today(),
            estado='borrador',
        )
        OrdenCompra.objects.create(
            vendor=self.vendor_a,
            numero='000005',
            fecha=date.today(),
            estado='borrador',
        )
        res = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        orden = OrdenCompra.objects.filter(vendor=self.vendor_a).latest('id')
        self.assertEqual(orden.numero, '000006')
