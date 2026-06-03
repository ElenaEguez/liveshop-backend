"""
Tests mínimos: pago simple legacy, pago mixto, validación, filtros y arqueo.
"""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import VentaPOS, VentaPOSPago, MetodoPago
from payments.utils import _calc_efectivo_esperado_turno
from products.models import Product, Inventory
from vendors.models import Vendor, Sucursal, Almacen, Caja, TurnoCaja

User = get_user_model()


class PosPagosMixtosBaseTestCase(APITestCase):
    """Setup común: vendor, sucursal, almacén, caja, turno, métodos de pago, producto."""

    def setUp(self):
        uid = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            email=f'vendor_{uid}@test.com',
            password='testpass123',
        )
        self.vendor = Vendor.objects.create(
            user=self.user,
            nombre_tienda=f'Tienda Test {uid}',
        )
        self.sucursal = Sucursal.objects.create(
            vendor=self.vendor,
            nombre='Sucursal Central',
            es_principal=True,
        )
        self.almacen = Almacen.objects.create(
            sucursal=self.sucursal,
            nombre='Almacén Principal',
        )
        self.caja = Caja.objects.create(
            sucursal=self.sucursal,
            nombre='Caja 1',
        )
        self.turno = TurnoCaja.objects.create(
            caja=self.caja,
            usuario=self.user,
            status='abierto',
            monto_apertura=Decimal('0'),
        )
        self.mp_efectivo = MetodoPago.objects.create(
            vendor=self.vendor,
            nombre='Efectivo',
            tipo='efectivo',
            orden=1,
        )
        self.mp_qr = MetodoPago.objects.create(
            vendor=self.vendor,
            nombre='QR',
            tipo='qr',
            orden=2,
        )
        self.mp_mixto = MetodoPago.objects.create(
            vendor=self.vendor,
            nombre='Mixto',
            tipo='mixto',
            orden=3,
        )
        self.product = Product.objects.create(
            vendor=self.vendor,
            name='Producto Test',
            price=Decimal('500.00'),
            sku=f'SKU-{uid}',
            stock=100,
        )
        self.inventory = Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=50,
            purchase_cost=Decimal('10.00'),
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.ventas_url = reverse('venta-pos-list')

    def _inventory_qty(self):
        self.inventory.refresh_from_db()
        return self.inventory.quantity

    def _venta_base_payload(self, precio, cantidad=1, **extra):
        payload = {
            'sucursal_id': self.sucursal.id,
            'caja_id': self.caja.id,
            'turno_id': self.turno.id,
            'items': [{
                'product_id': self.product.id,
                'cantidad': cantidad,
                'precio_unitario': str(precio),
            }],
        }
        payload.update(extra)
        return payload

    def _post_venta(self, **kwargs):
        return self.client.post(self.ventas_url, kwargs, format='json')

    def _venta_from_response(self, response):
        return VentaPOS.objects.get(pk=response.data['id'])


class TestVentaSimpleLegacy(PosPagosMixtosBaseTestCase):
    """TEST 1 — POST con metodo_pago_id crea una línea VentaPOSPago y descuenta stock."""

    def test_venta_simple_legacy(self):
        qty_before = self._inventory_qty()
        precio = Decimal('100.00')

        response = self._post_venta(
            **self._venta_base_payload(
                precio,
                metodo_pago_id=self.mp_efectivo.id,
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        venta = self._venta_from_response(response)
        pagos = VentaPOSPago.objects.filter(venta=venta)
        self.assertEqual(pagos.count(), 1)
        self.assertEqual(pagos.first().monto, venta.total)
        self.assertEqual(pagos.first().metodo_pago_id, self.mp_efectivo.id)
        self.assertEqual(self._inventory_qty(), qty_before - 1)


class TestVentaMixtaEfectivoQr(PosPagosMixtosBaseTestCase):
    """TEST 2 — Pago mixto: 2 líneas, suma = total, stock descontado."""

    def test_venta_mixta_efectivo_qr(self):
        qty_before = self._inventory_qty()
        total = Decimal('500.00')

        response = self._post_venta(
            **self._venta_base_payload(
                total,
                pagos=[
                    {'metodo_pago_id': self.mp_efectivo.id, 'monto': '300.00'},
                    {'metodo_pago_id': self.mp_qr.id, 'monto': '200.00'},
                ],
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        venta = self._venta_from_response(response)
        self.assertEqual(venta.total, total)
        pagos = list(VentaPOSPago.objects.filter(venta=venta).order_by('orden'))
        self.assertEqual(len(pagos), 2)
        self.assertEqual(sum(p.monto for p in pagos), total)
        self.assertEqual(self._inventory_qty(), qty_before - 1)


class TestValidacionSumaIncorrecta(PosPagosMixtosBaseTestCase):
    """TEST 3 — Suma de pagos distinta al total → 400 y stock intacto."""

    def test_pagos_suma_incorrecta_revierte(self):
        qty_before = self._inventory_qty()

        response = self._post_venta(
            **self._venta_base_payload(
                Decimal('500.00'),
                pagos=[
                    {'metodo_pago_id': self.mp_efectivo.id, 'monto': '250.00'},
                    {'metodo_pago_id': self.mp_qr.id, 'monto': '150.00'},
                ],
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(VentaPOS.objects.filter(vendor=self.vendor).count(), 0)
        self.assertEqual(self._inventory_qty(), qty_before)


class TestFiltroListadoEfectivo(PosPagosMixtosBaseTestCase):
    """TEST 4 — Filtro metodo_pago_tipo=efectivo incluye simple y mixta."""

    def test_filtro_efectivo_incluye_simple_y_mixta(self):
        r_simple = self._post_venta(
            **self._venta_base_payload(
                Decimal('100.00'),
                metodo_pago_id=self.mp_efectivo.id,
            ),
        )
        self.assertEqual(r_simple.status_code, status.HTTP_201_CREATED)
        id_simple = r_simple.data['id']

        r_mixta = self._post_venta(
            **self._venta_base_payload(
                Decimal('500.00'),
                pagos=[
                    {'metodo_pago_id': self.mp_efectivo.id, 'monto': '300.00'},
                    {'metodo_pago_id': self.mp_qr.id, 'monto': '200.00'},
                ],
            ),
        )
        self.assertEqual(r_mixta.status_code, status.HTTP_201_CREATED)
        id_mixta = r_mixta.data['id']

        response = self.client.get(
            self.ventas_url,
            {'metodo_pago_tipo': 'efectivo'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        ids = {v['id'] for v in results}
        self.assertIn(id_simple, ids)
        self.assertIn(id_mixta, ids)


class TestArqueoEfectivoEsperado(PosPagosMixtosBaseTestCase):
    """TEST 5 — Arqueo suma solo montos en efectivo (300 + 200 = 500)."""

    def test_calc_efectivo_esperado_turno(self):
        r1 = self._post_venta(
            **self._venta_base_payload(
                Decimal('300.00'),
                metodo_pago_id=self.mp_efectivo.id,
            ),
        )
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)

        r2 = self._post_venta(
            **self._venta_base_payload(
                Decimal('500.00'),
                pagos=[
                    {'metodo_pago_id': self.mp_efectivo.id, 'monto': '200.00'},
                    {'metodo_pago_id': self.mp_qr.id, 'monto': '300.00'},
                ],
            ),
        )
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)

        arqueo = _calc_efectivo_esperado_turno(self.turno)
        efectivo_ventas = arqueo['ventas_contado_efectivo']
        self.assertEqual(efectivo_ventas, Decimal('500'))


class TestArqueoVentasHistoricasSinPagos(PosPagosMixtosBaseTestCase):
    """TEST 6 — Venta sin filas VentaPOSPago usa fallback legacy en arqueo."""

    def test_calc_efectivo_fallback_sin_pagos(self):
        VentaPOS.objects.create(
            vendor=self.vendor,
            sucursal=self.sucursal,
            caja=self.caja,
            turno=self.turno,
            numero_ticket='T-LEGACY',
            cliente_nombre='Histórico',
            metodo_pago=self.mp_efectivo,
            subtotal=Decimal('150.00'),
            descuento=Decimal('0'),
            total=Decimal('150.00'),
            status='completada',
            es_credito=False,
            usuario=self.user,
        )
        self.assertEqual(
            VentaPOSPago.objects.filter(venta__vendor=self.vendor).count(),
            0,
        )

        arqueo = _calc_efectivo_esperado_turno(self.turno)
        self.assertEqual(arqueo['ventas_contado_efectivo'], Decimal('150'))
