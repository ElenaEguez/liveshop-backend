"""
Tests de listado de arqueos: turnos con ventas del período y filtro por vendedor.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import VentaPOS, VentaPOSPago, MetodoPago
from products.models import Product, Inventory
from vendors.models import Vendor, Sucursal, Almacen, Caja, TurnoCaja, TeamMember, CustomRole

User = get_user_model()


class ArqueosFiltrosBaseTestCase(APITestCase):
    def setUp(self):
        uid = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            email=f'owner_{uid}@test.com',
            password='testpass123',
            nombre='Dueño',
            apellido='Tienda',
        )
        self.vendedor = User.objects.create_user(
            email=f'vendedor_{uid}@test.com',
            password='testpass123',
            nombre='Cajero',
            apellido='Venta',
        )
        self.vendor = Vendor.objects.create(
            user=self.user,
            nombre_tienda=f'Tienda Arqueo {uid}',
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
            monto_apertura=Decimal('100'),
        )
        ayer = timezone.localtime() - timedelta(days=1)
        TurnoCaja.objects.filter(pk=self.turno.pk).update(fecha_apertura=ayer)

        self.mp_efectivo = MetodoPago.objects.create(
            vendor=self.vendor,
            nombre='Efectivo',
            tipo='efectivo',
            orden=1,
        )
        self.product = Product.objects.create(
            vendor=self.vendor,
            name='Producto Arqueo',
            price=Decimal('250.00'),
            sku=f'SKU-AQ-{uid}',
            stock=50,
        )
        Inventory.objects.create(
            product=self.product,
            almacen=self.almacen,
            quantity=20,
            purchase_cost=Decimal('10.00'),
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.arqueos_url = reverse('turno-caja-arqueos')
        self.ventas_url = reverse('venta-pos-list')

    def _crear_venta(self, usuario, monto=Decimal('250.00')):
        venta = VentaPOS.objects.create(
            vendor=self.vendor,
            sucursal=self.sucursal,
            caja=self.caja,
            turno=self.turno,
            numero_ticket=f'T-{usuario.id}-{timezone.now().timestamp():.0f}',
            cliente_nombre='Cliente',
            metodo_pago=self.mp_efectivo,
            subtotal=monto,
            descuento=Decimal('0'),
            total=monto,
            status='completada',
            es_credito=False,
            usuario=usuario,
        )
        VentaPOSPago.objects.create(
            venta=venta,
            metodo_pago=self.mp_efectivo,
            monto=monto,
            orden=0,
        )
        return venta


class TestArqueosTurnoConVentaHoy(ArqueosFiltrosBaseTestCase):
    """Turno abierto ayer con venta de hoy debe aparecer en arqueos periodo=hoy."""

    def test_turno_apertura_ayer_venta_hoy_visible(self):
        self._crear_venta(self.vendedor)

        response = self.client.get(self.arqueos_url, {'periodo': 'today'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

        turno_ids = {t['id'] for t in response.data['results']}
        self.assertIn(self.turno.id, turno_ids)

        turno_data = next(t for t in response.data['results'] if t['id'] == self.turno.id)
        self.assertEqual(turno_data['total_ventas'], '250.00')


class TestArqueosFiltroCajeroVendedor(ArqueosFiltrosBaseTestCase):
    """Filtro Usuario debe incluir turnos donde el cajero vendió, no solo quien abrió."""

    def test_filtro_cajero_por_vendedor_de_la_venta(self):
        self._crear_venta(self.vendedor)

        response = self.client.get(
            self.arqueos_url,
            {'periodo': 'today', 'cajero_id': self.vendedor.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)
        turno_ids = {t['id'] for t in response.data['results']}
        self.assertIn(self.turno.id, turno_ids)

    def test_filtro_cajero_apertura_sin_ventas_no_incluye_turno(self):
        otro = User.objects.create_user(
            email=f'otro_{uuid.uuid4().hex[:6]}@test.com',
            password='testpass123',
        )
        self._crear_venta(self.vendedor)

        response = self.client.get(
            self.arqueos_url,
            {'periodo': 'today', 'cajero_id': otro.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        turno_ids = {t['id'] for t in response.data['results']}
        self.assertNotIn(self.turno.id, turno_ids)


class TestArqueosFiltroRolVendedor(ArqueosFiltrosBaseTestCase):
    def test_filtro_rol_por_vendedor_de_la_venta(self):
        rol = CustomRole.objects.create(
            vendor=self.vendor,
            name='Vendedor POS',
            perm_arqueos=True,
            perm_ventas_pos=True,
        )
        TeamMember.objects.create(
            vendor=self.vendor,
            user=self.vendedor,
            custom_role=rol,
            is_active=True,
        )
        self._crear_venta(self.vendedor)

        response = self.client.get(
            self.arqueos_url,
            {'periodo': 'today', 'rol_id': rol.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        turno_ids = {t['id'] for t in response.data['results']}
        self.assertIn(self.turno.id, turno_ids)
