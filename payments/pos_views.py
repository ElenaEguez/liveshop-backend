"""
POS views: VentaPOS, TurnoCaja, GastoOperativo, CategoriaGasto, Cupon,
           búsqueda de productos, y validación de cupones.
"""
import re
from datetime import date, timedelta, datetime as dt
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Max, Q, Sum, Count, Case, When, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from vendors.models import Sucursal, Caja, TurnoCaja, MovimientoCaja, KardexMovimiento, Vendor
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user
from vendors.serializers import TurnoCajaSerializer, MovimientoCajaSerializer
from products.models import Inventory, ProductVariant
from products.serializers import ProductPOSSerializer

from .models import (
    VentaPOS, VentaPOSItem, MetodoPago, Cupon,
    CategoriaGasto, GastoOperativo, PagoCredito,
)
from .serializers import (
    VentaPOSSerializer, VentaPOSCreateSerializer,
    MetodoPagoSerializer, CuponSerializer,
    CategoriaGastoSerializer, GastoOperativoSerializer,
    PagoCreditoSerializer,
)


class POSPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def _vendor_or_403(user):
    vendor = get_vendor_for_user(user)
    if not vendor:
        raise PermissionDenied("Sin perfil de vendedor asociado.")
    return vendor


# ─── VentaPOS ────────────────────────────────────────────────────────────────

class VentaPOSViewSet(viewsets.GenericViewSet):
    """
    POST   /api/v1/pos/ventas/          → crear venta (valida stock, aplica cupón, genera ticket)
    GET    /api/v1/pos/ventas/          → listar ventas del vendor
    GET    /api/v1/pos/ventas/{pk}/     → detalle de venta
    PATCH  /api/v1/pos/ventas/{pk}/anular/ → anular venta del día (restaura stock)
    """
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = VentaPOSSerializer
    pagination_class = POSPagination

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        vendor = self._get_vendor()
        qs = VentaPOS.objects.filter(vendor=vendor).select_related(
            'sucursal', 'metodo_pago', 'cupon', 'caja', 'turno', 'usuario',
        ).prefetch_related(
            'items__product', 'items__variant',
        ).order_by('-created_at')

        p = self.request.query_params
        periodo = p.get('periodo')
        today = timezone.localdate()
        if periodo == 'today':
            qs = qs.filter(created_at__date=today)
        elif periodo == 'week':
            week_start = today - timedelta(days=today.weekday())
            qs = qs.filter(created_at__date__gte=week_start, created_at__date__lte=today)
        elif periodo == 'month':
            qs = qs.filter(created_at__year=today.year, created_at__month=today.month)
        elif periodo == 'year':
            qs = qs.filter(created_at__year=today.year)
        if p.get('sucursal_id'):
            qs = qs.filter(sucursal_id=p['sucursal_id'])
        if p.get('cajero_id'):
            qs = qs.filter(usuario_id=p['cajero_id'])
        if p.get('metodo_pago_tipo'):
            qs = qs.filter(metodo_pago__tipo=p['metodo_pago_tipo'])
        if p.get('fecha'):
            qs = qs.filter(created_at__date=p['fecha'])
        if p.get('status'):
            qs = qs.filter(status=p['status'])
        if p.get('search'):
            term = p['search']
            qs = qs.filter(
                Q(numero_ticket__icontains=term) | Q(cliente_nombre__icontains=term)
            )
        return qs

    def list(self, request):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(VentaPOSSerializer(page, many=True).data)
        return Response(VentaPOSSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        qs = self.get_queryset().exclude(status='anulada')
        requested_status = request.query_params.get('status')
        # Regla contable por defecto: solo ventas confirmadas/completadas
        if not requested_status:
            qs = qs.filter(status='completada')
        agg = qs.aggregate(total_ventas=Sum('total'), cantidad_ventas=Count('id'))
        cobrado_expr = Sum(
            Case(
                When(es_credito=False, then=Coalesce('monto_recibido', F('total'))),
                default=Decimal('0'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        total_cobrado_contado = qs.aggregate(total=cobrado_expr)['total'] or Decimal('0')
        total_cobrado_credito = Decimal('0')
        if requested_status == 'credito':
            total_cobrado_credito = (
                PagoCredito.objects.filter(venta__in=qs).aggregate(total=Sum('monto'))['total'] or Decimal('0')
            )
        return Response({
            'total_ventas': str(agg['total_ventas'] or Decimal('0')),
            'total_cobrado': str(total_cobrado_contado + total_cobrado_credito),
            'cantidad_ventas': agg['cantidad_ventas'] or 0,
        })

    def retrieve(self, request, pk=None):
        venta = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(VentaPOSSerializer(venta).data)

    def create(self, request):
        vendor = self._get_vendor()
        ser = VentaPOSCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        with transaction.atomic():
            # ── Sucursal ─────────────────────────────────────────────────────
            sucursal = get_object_or_404(Sucursal, id=data['sucursal_id'], vendor=vendor)

            # Método de inventario configurado por el vendor (PEPS/UEPS/promedio)
            inv_method = vendor.inventory_method  # 'peps', 'ueps', 'promedio'

            # ── 1. Validar stock total (lock rows) ────────────────────────────
            items_data = data['items']
            # inventories_by_product: pid → lista de lotes en orden de consumo
            inventories_by_product: dict[int, list[Inventory]] = {}
            for item in items_data:
                pid = item['product_id']
                # Ordenar lotes según método de inventario
                order = 'created_at' if inv_method == 'peps' else '-created_at'
                lotes = list(
                    Inventory.objects.select_for_update().filter(
                        product_id=pid, product__vendor=vendor,
                        is_active=True, quantity__gt=0,
                    ).order_by(order)
                )
                total_avail = sum(
                    max(0, l.quantity - l.reserved_quantity) for l in lotes
                )
                if not lotes or total_avail < item['cantidad']:
                    nombre = lotes[0].product.name if lotes else f'product_id={pid}'
                    raise ValidationError({
                        'items': (
                            f"Stock insuficiente para '{nombre}': "
                            f"disponible {total_avail}, solicitado {item['cantidad']}."
                        )
                    })
                inventories_by_product[pid] = lotes

                # Si el producto tiene variantes activas, variant_id es obligatorio
                if not item.get('variant_id'):
                    tiene_variantes = ProductVariant.objects.filter(
                        product_id=pid, is_active=True
                    ).exists()
                    if tiene_variantes:
                        nombre = lotes[0].product.name if lotes else f'product_id={pid}'
                        raise ValidationError({
                            'items': f"El producto '{nombre}' tiene variantes. Debe seleccionar una variante."
                        })

            # ── 2. Validar cupón ──────────────────────────────────────────────
            cupon = None
            descuento_cupon = Decimal('0')
            cupon_codigo = data.get('cupon_codigo')
            if cupon_codigo:
                try:
                    cupon = Cupon.objects.get(
                        codigo=cupon_codigo, vendor=vendor, activo=True)
                except Cupon.DoesNotExist:
                    raise ValidationError({'cupon_codigo': 'Cupón inválido o inactivo.'})
                if cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos:
                    raise ValidationError({'cupon_codigo': 'Cupón agotado.'})
                if cupon.fecha_vencimiento and cupon.fecha_vencimiento < date.today():
                    raise ValidationError({'cupon_codigo': 'Cupón vencido.'})
                if not cupon.aplica_pos:
                    raise ValidationError(
                        {'cupon_codigo': 'Este cupón no aplica para ventas POS.'})

            # ── 3. Calcular totales ───────────────────────────────────────────
            subtotal = sum(
                item['precio_unitario'] * item['cantidad'] for item in items_data
            )
            discount_pct = data.get('discount_percentage')
            if discount_pct and discount_pct > 0:
                descuento_manual = (subtotal * discount_pct / 100).quantize(Decimal('0.01'))
                data['discount_type'] = 'PERCENT'
            else:
                descuento_manual = data.get('descuento', Decimal('0'))
            base = max(subtotal - descuento_manual, Decimal('0'))

            if cupon:
                if cupon.tipo == 'porcentaje':
                    descuento_cupon = (base * cupon.valor / 100).quantize(Decimal('0.01'))
                else:
                    descuento_cupon = min(cupon.valor, base)

            total = max(base - descuento_cupon, Decimal('0'))
            monto_recibido = data.get('monto_recibido')
            vuelto = max(
                (monto_recibido or Decimal('0')) - total, Decimal('0')
            )

            # ── 4. Generar numero_ticket ──────────────────────────────────────
            last = VentaPOS.objects.filter(vendor=vendor).aggregate(m=Max('numero_ticket'))['m']
            num = (int(re.sub(r'\D', '', last) or 0) + 1) if last else 1
            numero_ticket = f"T{num:04d}"

            # ── 5. Fecha vencimiento crédito ──────────────────────────────────
            fecha_venc = None
            if data.get('es_credito') and data.get('plazo_dias'):
                fecha_venc = date.today() + timedelta(days=data['plazo_dias'])

            # ── 6. MetodoPago ─────────────────────────────────────────────────
            metodo_pago = None
            if data.get('metodo_pago_id'):
                metodo_pago = get_object_or_404(
                    MetodoPago, id=data['metodo_pago_id'], vendor=vendor, activo=True)

            # ── 7. Caja y turno ───────────────────────────────────────────────
            caja = None
            turno = None
            if not data.get('caja_id') or not data.get('turno_id'):
                raise ValidationError({'error': 'Debe seleccionar caja y turno abierto para registrar la venta.'})
            caja = get_object_or_404(
                Caja, id=data['caja_id'], sucursal__vendor=vendor)
            turno = get_object_or_404(
                TurnoCaja, id=data['turno_id'],
                caja__sucursal__vendor=vendor, status='abierto')
            if turno.caja_id != caja.id:
                raise ValidationError({'error': 'El turno abierto no corresponde a la caja seleccionada.'})

            # ── 8. Crear VentaPOS ─────────────────────────────────────────────
            venta = VentaPOS.objects.create(
                vendor=vendor,
                sucursal=sucursal,
                caja=caja,
                turno=turno,
                numero_ticket=numero_ticket,
                cliente_nombre=data.get('cliente_nombre', 'Genérico'),
                cliente_telefono=data.get('cliente_telefono', ''),
                metodo_pago=metodo_pago,
                subtotal=subtotal,
                descuento=descuento_manual + descuento_cupon,
                discount_percentage=data.get('discount_percentage'),
                discount_type=data.get('discount_type'),
                canal_venta=data.get('canal_venta', 'TIENDA'),
                direccion_envio=data.get('direccion_envio'),
                total=total,
                monto_recibido=monto_recibido,
                vuelto=vuelto,
                cupon=cupon,
                status='credito' if data.get('es_credito') else 'completada',
                usuario=request.user,
                es_credito=data.get('es_credito', False),
                plazo_dias=data.get('plazo_dias'),
                fecha_vencimiento_credito=fecha_venc,
                notas=data.get('notas', ''),
            )

            # ── 9. Items + descuento de stock por lote (PEPS/UEPS) + kardex ────
            for item in items_data:
                pid = item['product_id']
                lotes = inventories_by_product[pid]

                variant = None
                if item.get('variant_id'):
                    variant = get_object_or_404(
                        ProductVariant,
                        id=item['variant_id'], product_id=pid,
                    )
                    # Descontar stock propio de la variante si lo tiene registrado
                    if variant.stock_extra > 0:
                        variant.stock_extra = max(0, variant.stock_extra - item['cantidad'])
                        variant.save(update_fields=['stock_extra'])

                cantidad_pendiente = item['cantidad']
                precio = item['precio_unitario']

                # Costos ponderados para calcular costo_unitario del item
                costo_total_lotes = Decimal('0')
                cantidad_costeada = 0

                for lote in lotes:
                    if cantidad_pendiente <= 0:
                        break
                    disponible = lote.quantity - lote.reserved_quantity
                    if disponible <= 0:
                        continue

                    consumir = min(cantidad_pendiente, disponible)
                    stock_anterior = lote.quantity
                    lote.quantity -= consumir
                    lote.save(update_fields=['quantity'])

                    costo_lote = lote.purchase_cost or Decimal('0')
                    costo_total_lotes += costo_lote * consumir
                    cantidad_costeada += consumir

                    KardexMovimiento.objects.create(
                        inventory=lote,
                        almacen=lote.almacen,
                        tipo='salida',
                        motivo='venta',
                        cantidad=-consumir,
                        stock_anterior=stock_anterior,
                        stock_actual=lote.quantity,
                        costo_promedio=costo_lote,
                        documento_ref=numero_ticket,
                        usuario=request.user,
                        variant=variant,
                        notas=f'Venta POS {numero_ticket} [{inv_method.upper()}]',
                    )
                    cantidad_pendiente -= consumir

                # Costo unitario ponderado del item completo
                costo_unitario = (
                    (costo_total_lotes / item['cantidad']).quantize(Decimal('0.0001'))
                    if item['cantidad'] > 0 else Decimal('0')
                )

                VentaPOSItem.objects.create(
                    venta=venta,
                    product_id=pid,
                    variant=variant,
                    cantidad=item['cantidad'],
                    precio_unitario=precio,
                    costo_unitario=costo_unitario,
                    subtotal=precio * item['cantidad'],
                )

            # ── 10. Actualizar usos del cupón ─────────────────────────────────
            if cupon:
                Cupon.objects.filter(pk=cupon.pk).update(
                    usos_actuales=F('usos_actuales') + 1)

        venta.refresh_from_db()
        return Response(VentaPOSSerializer(venta).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch', 'post'])
    def anular(self, request, pk=None):
        venta = get_object_or_404(VentaPOS, pk=pk, vendor=self._get_vendor())

        if venta.status == 'anulada':
            return Response(
                {'error': 'La venta ya está anulada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.localtime(venta.created_at).date() != timezone.localdate():
            return Response(
                {'error': 'Solo se pueden anular ventas del día actual.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for item in venta.items.select_related('product').all():
                if not item.product:
                    continue
                inv = Inventory.objects.select_for_update().filter(
                    product=item.product, is_active=True,
                ).first()
                if inv:
                    stock_anterior = inv.quantity
                    inv.quantity += item.cantidad
                    inv.save(update_fields=['quantity'])
                    KardexMovimiento.objects.create(
                        inventory=inv,
                        almacen=inv.almacen,
                        tipo='entrada',
                        motivo='devolucion',
                        cantidad=item.cantidad,
                        stock_anterior=stock_anterior,
                        stock_actual=inv.quantity,
                        documento_ref=venta.numero_ticket,
                        usuario=request.user,
                        notas=f'Anulación venta POS {venta.numero_ticket}',
                    )

            if venta.cupon_id:
                Cupon.objects.filter(pk=venta.cupon_id).update(
                    usos_actuales=F('usos_actuales') - 1)

            venta.status = 'anulada'
            venta.save(update_fields=['status'])

        venta.refresh_from_db()
        return Response(VentaPOSSerializer(venta).data)

    @action(detail=True, methods=['patch', 'post'], url_path='cobrar-credito')
    def cobrar_credito(self, request, pk=None):
        """Marks a credit sale as paid (completada)."""
        venta = get_object_or_404(VentaPOS, pk=pk, vendor=self._get_vendor())

        if venta.status != 'credito':
            return Response(
                {'error': 'Solo se pueden cobrar ventas en estado crédito.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        metodo_pago_id = request.data.get('metodo_pago_id')
        monto_recibido = request.data.get('monto_recibido')

        venta.status = 'completada'
        if metodo_pago_id:
            from .models import MetodoPago
            mp = MetodoPago.objects.filter(pk=metodo_pago_id, vendor=self._get_vendor()).first()
            if mp:
                venta.metodo_pago = mp
        if monto_recibido is not None:
            venta.monto_recibido = monto_recibido
        venta.save(update_fields=['status', 'metodo_pago', 'monto_recibido'])

        venta.refresh_from_db()
        return Response(VentaPOSSerializer(venta).data)

    @action(detail=True, methods=['get', 'post'], url_path='pagos-credito')
    def pagos_credito(self, request, pk=None):
        """
        GET  → lista todos los pagos parciales de la venta a crédito.
        POST → registra un nuevo pago parcial:
               { monto, metodo_pago_id (opt), notas (opt) }
               Si el saldo llega a 0, la venta pasa a 'completada'.
        """
        venta = get_object_or_404(VentaPOS, pk=pk, vendor=self._get_vendor())

        if request.method == 'GET':
            pagos = venta.pagos_credito.all()
            return Response(PagoCreditoSerializer(pagos, many=True).data)

        # POST — registrar pago parcial
        if venta.status != 'credito':
            return Response(
                {'error': 'Esta venta no está en estado crédito.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            monto = Decimal(str(request.data.get('monto', 0)))
        except Exception:
            return Response({'error': 'Monto inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        if monto <= 0:
            return Response({'error': 'El monto debe ser mayor a 0.'}, status=status.HTTP_400_BAD_REQUEST)

        pagado = venta.pagos_credito.aggregate(t=Sum('monto'))['t'] or Decimal('0')
        saldo = venta.total - pagado

        if monto > saldo:
            return Response(
                {'error': f'El monto ({monto}) excede el saldo pendiente ({saldo}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        metodo_pago_id = request.data.get('metodo_pago_id')
        metodo_pago = None
        if metodo_pago_id:
            metodo_pago = MetodoPago.objects.filter(pk=metodo_pago_id, vendor=self._get_vendor()).first()

        pago = PagoCredito.objects.create(
            venta=venta,
            monto=monto,
            metodo_pago=metodo_pago,
            notas=request.data.get('notas', ''),
            usuario=request.user,
        )

        # Si el saldo queda en 0 → completar la venta
        nuevo_pagado = pagado + monto
        if nuevo_pagado >= venta.total:
            venta.status = 'completada'
            venta.save(update_fields=['status'])

        venta.refresh_from_db()
        return Response({
            'pago': PagoCreditoSerializer(pago).data,
            'venta': VentaPOSSerializer(venta).data,
        }, status=status.HTTP_201_CREATED)


# ─── Buscar producto POS ──────────────────────────────────────────────────────

class ProductoPOSSearchView(APIView):
    """
    GET /api/v1/pos/buscar-producto/?q={texto_o_barcode}&sucursal_id={id}
    Devuelve máximo 10 resultados con stock disponible y variantes.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendor = _vendor_or_403(request.user)
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response({'error': 'Parámetro q requerido.'}, status=400)

        from products.models import Product
        qs = Product.objects.filter(vendor=vendor, is_active=True).prefetch_related(
            'images', 'variant_objects', 'inventories',
        )
        # Barcode exacto primero, luego nombre
        qs = qs.filter(Q(barcode=q) | Q(name__icontains=q))[:10]

        ser = ProductPOSSerializer(qs, many=True, context={'request': request})
        return Response(ser.data)


class POSScanView(APIView):
    """
    GET /api/v1/pos/scan/?code={valor}
    Busca producto por código de barras con prioridad exacta.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendor = _vendor_or_403(request.user)
        code = request.query_params.get('code', '').strip()
        if not code:
            return Response({'error': 'Parámetro code requerido.'}, status=400)

        from products.models import Product
        from products.serializers import ProductPOSSerializer

        # Filtrar por vendor y activos — prefetch inventories y variantes para stock_disponible
        base_qs = Product.objects.filter(vendor=vendor, is_active=True).select_related('category').prefetch_related(
            'images', 'inventories', 'variant_objects'
        )

        # 1. Buscar match exacto en orden de prioridad
        exact_match = None
        if base_qs.filter(barcode=code).exists():
            exact_match = base_qs.filter(barcode=code).first()
        elif base_qs.filter(internal_code=code).exists():
            exact_match = base_qs.filter(internal_code=code).first()
        elif base_qs.filter(sku=code).exists():
            exact_match = base_qs.filter(sku=code).first()

        if exact_match:
            ser = ProductPOSSerializer(exact_match, context={'request': request})
            return Response({
                'match': 'exact',
                'product': ser.data
            })

        # 2. Si no hay match exacto, buscar parcial
        partial_qs = list(base_qs.filter(
            Q(name__icontains=code) |
            Q(barcode__icontains=code) |
            Q(internal_code__icontains=code)
        )[:10])

        if not partial_qs:
            return Response({'match': 'none'})

        ser = ProductPOSSerializer(partial_qs, many=True, context={'request': request})
        return Response({
            'match': 'partial',
            'products': ser.data
        })


# ─── TurnoCaja ────────────────────────────────────────────────────────────────

class TurnoCajaViewSet(viewsets.GenericViewSet):
    """
    POST /api/v1/pos/turnos/abrir/          → abrir turno
    POST /api/v1/pos/turnos/{pk}/cerrar/    → cerrar turno
    GET  /api/v1/pos/turnos/activo/?caja_id → turno abierto de la caja
    GET  /api/v1/pos/turnos/{pk}/resumen/   → resumen completo del turno
    """
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = TurnoCajaSerializer

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        vendor = self._get_vendor()
        return TurnoCaja.objects.filter(
            caja__sucursal__vendor=vendor
        ).select_related('caja__sucursal', 'usuario')

    @action(detail=False, methods=['post'])
    def abrir(self, request):
        vendor = self._get_vendor()
        caja_id = request.data.get('caja_id')
        monto_apertura = request.data.get('monto_apertura', 0)

        if not caja_id:
            return Response({'error': 'caja_id es requerido.'}, status=400)

        caja = get_object_or_404(Caja, id=caja_id, sucursal__vendor=vendor, activa=True)

        if TurnoCaja.objects.filter(caja=caja, status='abierto').exists():
            return Response(
                {'error': 'Ya existe un turno abierto para esta caja.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        turno = TurnoCaja.objects.create(
            caja=caja,
            usuario=request.user,
            status='abierto',
            monto_apertura=monto_apertura,
        )
        return Response(TurnoCajaSerializer(turno).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cerrar(self, request, pk=None):
        turno = get_object_or_404(self.get_queryset(), pk=pk)

        if turno.status != 'abierto':
            return Response(
                {'error': 'El turno no está abierto.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        monto_cierre = request.data.get('monto_cierre')
        notas_cierre = request.data.get('notas_cierre', '')

        if monto_cierre is None:
            return Response({'error': 'monto_cierre es requerido.'}, status=400)

        with transaction.atomic():
            monto_cierre_dec = Decimal(str(monto_cierre))
            # Calcular efectivo esperado antes de guardar
            ventas_ef = VentaPOS.objects.filter(
                turno=turno, status='completada', metodo_pago__tipo='efectivo'
            ).aggregate(t=Sum('total'))['t'] or Decimal('0')
            ingresos = MovimientoCaja.objects.filter(turno=turno, tipo='ingreso').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            retiros  = MovimientoCaja.objects.filter(turno=turno, tipo='retiro').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            efect_esp = turno.monto_apertura + ventas_ef + ingresos - retiros

            turno.status = 'cerrado'
            turno.monto_cierre = monto_cierre_dec
            turno.efectivo_esperado = efect_esp
            turno.diferencia_cierre = monto_cierre_dec - efect_esp
            turno.fecha_cierre = timezone.now()
            turno.notas_cierre = notas_cierre
            turno.save()

        ventas_agg = VentaPOS.objects.filter(
            turno=turno, status='completada'
        ).aggregate(total=Sum('total'), cantidad=Count('id'))

        return Response({
            'turno': TurnoCajaSerializer(turno).data,
            'resumen': {
                'total_ventas': str(ventas_agg['total'] or Decimal('0')),
                'cantidad_ventas': ventas_agg['cantidad'] or 0,
                'monto_apertura': str(turno.monto_apertura),
                'monto_cierre': str(turno.monto_cierre),
                'efectivo_esperado': str(efect_esp),
                'diferencia': str(turno.diferencia_cierre),
            },
        })

    @action(detail=False, methods=['get'])
    def activo(self, request):
        vendor = self._get_vendor()
        caja_id = request.query_params.get('caja_id')
        if not caja_id:
            return Response({'error': 'caja_id es requerido.'}, status=400)

        caja = get_object_or_404(Caja, id=caja_id, sucursal__vendor=vendor)
        turno = TurnoCaja.objects.filter(caja=caja, status='abierto').first()

        if not turno:
            return Response({'turno': None})
        return Response({'turno': TurnoCajaSerializer(turno).data})

    @action(detail=True, methods=['get'])
    def resumen(self, request, pk=None):
        turno = get_object_or_404(self.get_queryset(), pk=pk)

        ventas = VentaPOS.objects.filter(
            turno=turno, status='completada'
        ).select_related('metodo_pago')

        agg = ventas.aggregate(total=Sum('total'), cantidad=Count('id'))
        total_ventas = agg['total'] or Decimal('0')

        # Ventas agrupadas por método de pago
        por_metodo: dict = {}
        total_ventas_efectivo = Decimal('0')
        for v in ventas:
            tipo_mp = v.metodo_pago.tipo if v.metodo_pago else ''
            nombre = v.metodo_pago.nombre if v.metodo_pago else 'Sin método'
            if nombre not in por_metodo:
                por_metodo[nombre] = {'total': Decimal('0'), 'cantidad': 0}
            por_metodo[nombre]['total'] += v.total
            por_metodo[nombre]['cantidad'] += 1
            if tipo_mp == 'efectivo':
                total_ventas_efectivo += v.total

        # Movimientos manuales de caja
        movs = MovimientoCaja.objects.filter(turno=turno)
        total_ingresos = movs.filter(tipo='ingreso').aggregate(t=Sum('monto'))['t'] or Decimal('0')
        total_retiros  = movs.filter(tipo='retiro').aggregate(t=Sum('monto'))['t'] or Decimal('0')

        efectivo_esperado = turno.monto_apertura + total_ventas_efectivo + total_ingresos - total_retiros

        return Response({
            'turno': TurnoCajaSerializer(turno).data,
            'total_ventas': str(total_ventas),
            'cantidad_ventas': agg['cantidad'] or 0,
            'total_ventas_efectivo': str(total_ventas_efectivo),
            'total_ingresos': str(total_ingresos),
            'total_retiros': str(total_retiros),
            'efectivo_esperado': str(efectivo_esperado),
            'diferencia': str(
                (turno.monto_cierre or Decimal('0')) - efectivo_esperado
            ),
            'ventas_por_metodo': [
                {'metodo': k, 'total': str(v['total']), 'cantidad': v['cantidad']}
                for k, v in por_metodo.items()
            ],
        })

    @action(detail=True, methods=['post'])
    def movimiento(self, request, pk=None):
        """POST /api/v1/pos/turnos/{pk}/movimiento/  — registra ingreso o retiro de caja."""
        turno = get_object_or_404(TurnoCaja, pk=pk, caja__sucursal__vendor=self._get_vendor())
        if turno.status != 'abierto':
            return Response({'error': 'El turno ya está cerrado.'}, status=status.HTTP_400_BAD_REQUEST)

        tipo = request.data.get('tipo')
        concepto = request.data.get('concepto', '').strip()
        monto = request.data.get('monto')

        if tipo not in ('ingreso', 'retiro'):
            return Response({'error': 'tipo debe ser ingreso o retiro.'}, status=400)
        if not concepto:
            return Response({'error': 'concepto es requerido.'}, status=400)
        if not monto or Decimal(str(monto)) <= 0:
            return Response({'error': 'monto debe ser mayor a 0.'}, status=400)

        mov = MovimientoCaja.objects.create(
            turno=turno,
            tipo=tipo,
            concepto=concepto,
            monto=Decimal(str(monto)),
            usuario=request.user,
        )
        return Response(MovimientoCajaSerializer(mov).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch', 'post'], url_path='editar-fondo')
    def editar_fondo(self, request, pk=None):
        """PATCH /api/v1/pos/turnos/{pk}/editar-fondo/ — edita el monto de apertura de un turno abierto."""
        from decimal import InvalidOperation
        turno = self.get_object()

        if turno.status != 'abierto':
            return Response(
                {'error': 'Solo se puede editar el fondo de un turno abierto.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        nuevo_fondo = request.data.get('fondo_inicial')
        if nuevo_fondo is None:
            return Response(
                {'error': 'Debe proporcionar fondo_inicial.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            fondo = Decimal(str(nuevo_fondo))
            if fondo < 0:
                return Response(
                    {'error': 'El fondo inicial no puede ser negativo.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            turno.monto_apertura = fondo
            turno.save(update_fields=['monto_apertura'])
            return Response({'fondo_inicial': str(turno.monto_apertura)})
        except InvalidOperation:
            return Response(
                {'error': 'Valor inválido para fondo inicial.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=False, methods=['get'])
    def list_turnos(self, request):
        """GET /api/v1/pos/turnos/list_turnos/?periodo=today|week|month|year"""
        vendor = self._get_vendor()
        qs = self.get_queryset()

        periodo = request.query_params.get('periodo', 'today')
        today = timezone.localdate()
        if periodo == 'today':
            qs = qs.filter(fecha_apertura__date=today)
        elif periodo == 'week':
            qs = qs.filter(fecha_apertura__date__gte=today - timedelta(days=7))
        elif periodo == 'month':
            qs = qs.filter(fecha_apertura__date__gte=today - timedelta(days=30))
        elif periodo == 'year':
            qs = qs.filter(fecha_apertura__year=today.year)

        serializer = TurnoCajaSerializer(qs.order_by('-fecha_apertura'), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def arqueos(self, request):
        """
        GET /api/v1/pos/turnos/arqueos/
        Params: periodo, page, page_size, semana, cajero_id, sucursal_id, metodo_pago_tipo
        Returns paginated turnos + totales_por_cajero + totales_por_metodo.
        """
        vendor = self._get_vendor()
        qs = TurnoCaja.objects.filter(
            caja__sucursal__vendor=vendor
        ).select_related('caja__sucursal', 'usuario').order_by('-fecha_apertura')

        periodo = request.query_params.get('periodo', 'month')
        today = timezone.localdate()
        ventas_periodo_filter = Q()
        if periodo == 'today':
            qs = qs.filter(fecha_apertura__date=today)
            ventas_periodo_filter = Q(created_at__date=today)
        elif periodo == 'week':
            week_start = today - timedelta(days=today.weekday())  # lunes
            qs = qs.filter(fecha_apertura__date__gte=week_start, fecha_apertura__date__lte=today)
            ventas_periodo_filter = Q(created_at__date__gte=week_start, created_at__date__lte=today)
        elif periodo == 'month':
            qs = qs.filter(fecha_apertura__year=today.year, fecha_apertura__month=today.month)
            ventas_periodo_filter = Q(created_at__year=today.year, created_at__month=today.month)
        elif periodo == 'year':
            qs = qs.filter(fecha_apertura__year=today.year)
            ventas_periodo_filter = Q(created_at__year=today.year)

        # Incluye turnos que tengan ventas en el período aunque se hayan abierto fuera del rango.
        if ventas_periodo_filter:
            turno_ids_from_sales = list(
                VentaPOS.objects.filter(
                    ventas_periodo_filter,
                    vendor=vendor,
                    turno_id__isnull=False,
                ).values_list('turno_id', flat=True).distinct()
            )
            if turno_ids_from_sales:
                qs = TurnoCaja.objects.filter(
                    Q(id__in=qs.values_list('id', flat=True)) | Q(id__in=turno_ids_from_sales),
                    caja__sucursal__vendor=vendor,
                ).select_related('caja__sucursal', 'usuario').order_by('-fecha_apertura')

        # Filtro semana del mes (solo periodo=month)
        semana_param = request.query_params.get('semana')
        if semana_param and periodo == 'month':
            try:
                semana = int(semana_param)
                if 1 <= semana <= 5:
                    dia_inicio = (semana - 1) * 7 + 1
                    dia_fin = min(semana * 7, 31)
                    qs = qs.filter(
                        fecha_apertura__day__gte=dia_inicio,
                        fecha_apertura__day__lte=dia_fin,
                    )
            except (ValueError, TypeError):
                pass

        # Filtros opcionales
        cajero_id = request.query_params.get('cajero_id')
        if cajero_id:
            qs = qs.filter(usuario_id=cajero_id)

        sucursal_id = request.query_params.get('sucursal_id')
        if sucursal_id:
            qs = qs.filter(caja__sucursal_id=sucursal_id)
        metodo_pago_tipo = request.query_params.get('metodo_pago_tipo')

        # ── Totales agregados sobre el período completo (sin paginar) ─────────
        turno_ids = list(qs.values_list('id', flat=True))

        # Ventas agrupadas por cajero + método
        ventas_filter = {'turno_id__in': turno_ids, 'status': 'completada'}
        if metodo_pago_tipo:
            ventas_filter['metodo_pago__tipo'] = metodo_pago_tipo

        ventas_cajero_qs = (
            VentaPOS.objects.filter(**ventas_filter)
            .values(
                'usuario__id', 'usuario__first_name',
                'usuario__last_name', 'usuario__email',
                'metodo_pago__tipo', 'metodo_pago__nombre',
            )
            .annotate(subtotal_venta=Sum('total'), cant=Count('id'))
        )

        cajero_map: dict = {}
        for row in ventas_cajero_qs:
            uid = row['usuario__id']
            if uid not in cajero_map:
                first = row['usuario__first_name'] or ''
                last  = row['usuario__last_name'] or ''
                name  = f'{first} {last}'.strip() or row['usuario__email'] or '—'
                cajero_map[uid] = {
                    'id': uid, 'nombre': name,
                    'total': Decimal('0'), 'por_metodo': {},
                }
            monto = row['subtotal_venta'] or Decimal('0')
            cajero_map[uid]['total'] += monto
            tipo  = row['metodo_pago__tipo'] or 'otro'
            mnombre = row['metodo_pago__nombre'] or 'Otro'
            pm = cajero_map[uid]['por_metodo'].setdefault(
                tipo, {'nombre': mnombre, 'total': Decimal('0'), 'cantidad': 0}
            )
            pm['total'] += monto
            pm['cantidad'] += row['cant'] or 0

        totales_por_cajero = [
            {
                'id': v['id'],
                'nombre': v['nombre'],
                'total': str(round(v['total'], 2)),
                'por_metodo': [
                    {'tipo': k, 'nombre': m['nombre'],
                     'total': str(round(m['total'], 2)), 'cantidad': m['cantidad']}
                    for k, m in v['por_metodo'].items()
                ],
            }
            for v in cajero_map.values()
        ]

        # Totales globales por método
        ventas_metodo_qs = (
            VentaPOS.objects.filter(**ventas_filter)
            .values('metodo_pago__tipo', 'metodo_pago__nombre')
            .annotate(total=Sum('total'), cantidad=Count('id'))
            .order_by('-total')
        )
        totales_por_metodo = [
            {
                'tipo': r['metodo_pago__tipo'] or 'otro',
                'nombre': r['metodo_pago__nombre'] or 'Otro',
                'total': str(round(r['total'] or Decimal('0'), 2)),
                'cantidad': r['cantidad'],
            }
            for r in ventas_metodo_qs
        ]

        # ── Paginación ────────────────────────────────────────────────────────
        page_size = min(int(request.query_params.get('page_size', 20)), 100)
        page_num  = max(int(request.query_params.get('page', 1)), 1)
        total_count = qs.count()
        start = (page_num - 1) * page_size
        qs_page = qs[start:start + page_size]

        return Response({
            'count': total_count,
            'page': page_num,
            'pages': max(1, -(-total_count // page_size)),
            'results': TurnoCajaSerializer(qs_page, many=True).data,
            'totales_por_cajero': totales_por_cajero,
            'totales_por_metodo': totales_por_metodo,
        })


# ─── Gastos Operativos ────────────────────────────────────────────────────────

class GastoViewSet(viewsets.ModelViewSet):
    """
    GET/POST   /api/v1/gastos/       Filtros: periodo, fecha, categoria_id
    PATCH/DELETE /api/v1/gastos/{pk}/
    """
    serializer_class = GastoOperativoSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    pagination_class = POSPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        vendor = self._get_vendor()
        qs = GastoOperativo.objects.filter(vendor=vendor).select_related(
            'categoria', 'sucursal', 'usuario',
        ).order_by('-fecha', '-created_at')

        p = self.request.query_params
        today = timezone.localdate()

        periodo = p.get('periodo')
        if periodo == 'today':
            qs = qs.filter(fecha=today)
        elif periodo == 'week':
            start = today - timedelta(days=today.weekday())
            qs = qs.filter(fecha__gte=start, fecha__lte=today)
        elif periodo == 'month':
            qs = qs.filter(fecha__year=today.year, fecha__month=today.month)
        elif periodo == 'year':
            qs = qs.filter(fecha__year=today.year)

        if p.get('fecha'):
            qs = qs.filter(fecha=p['fecha'])
        if p.get('categoria_id'):
            qs = qs.filter(categoria_id=p['categoria_id'])

        return qs

    def perform_create(self, serializer):
        vendor = self._get_vendor()
        serializer.save(vendor=vendor, usuario=self.request.user)


class CategoriaGastoViewSet(viewsets.ModelViewSet):
    """
    GET/POST/DELETE /api/v1/gastos/categorias/
    """
    serializer_class = CategoriaGastoSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        return CategoriaGasto.objects.filter(vendor=self._get_vendor())

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())


# ─── Cupones ──────────────────────────────────────────────────────────────────

class CuponViewSet(viewsets.ModelViewSet):
    """
    GET/POST/PATCH/DELETE /api/v1/cupones/
    """
    serializer_class = CuponSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        return Cupon.objects.filter(vendor=self._get_vendor())

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())


# ─── Métodos de Pago ──────────────────────────────────────────────────────────

class MetodoPagoViewSet(viewsets.ModelViewSet):
    """
    GET/POST/PATCH/DELETE /api/v1/pos/metodos-pago/
    """
    serializer_class = MetodoPagoSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _vendor_or_403(self.request.user)

    def get_queryset(self):
        return MetodoPago.objects.filter(
            vendor=self._get_vendor(), activo=True
        ).order_by('orden', 'nombre')

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())


class CuponValidarView(APIView):
    """
    GET /api/v1/cupones/validar/?codigo={codigo}&total={monto}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendor = _vendor_or_403(request.user)
        codigo = request.query_params.get('codigo', '').strip()
        if not codigo:
            return Response({'error': 'Parámetro codigo requerido.'}, status=400)

        try:
            total = Decimal(str(request.query_params.get('total', '0')))
        except Exception:
            total = Decimal('0')

        try:
            cupon = Cupon.objects.get(codigo=codigo, vendor=vendor, activo=True)
        except Cupon.DoesNotExist:
            return Response({'valido': False, 'error': 'Cupón inválido o inactivo.'})

        if cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos:
            return Response({'valido': False, 'error': 'Cupón agotado.'})
        if cupon.fecha_vencimiento and cupon.fecha_vencimiento < date.today():
            return Response({'valido': False, 'error': 'Cupón vencido.'})
        if not cupon.aplica_pos:
            return Response({'valido': False, 'error': 'Cupón no aplica para POS.'})

        if cupon.tipo == 'porcentaje':
            descuento = (total * cupon.valor / 100).quantize(Decimal('0.01'))
        else:
            descuento = min(cupon.valor, total)

        return Response({
            'valido': True,
            'descuento_aplicado': str(descuento),
            'cupon_data': CuponSerializer(cupon).data,
        })


class PublicCuponValidarView(APIView):
    """
    GET /api/v1/cupones/public/validar/?vendor_slug={slug}&codigo={code}&total={amount}
    No authentication required — used from the public live page.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        vendor_slug = request.query_params.get('vendor_slug', '').strip()
        codigo = request.query_params.get('codigo', '').strip()
        if not vendor_slug or not codigo:
            return Response({'valido': False, 'error': 'Parámetros requeridos: vendor_slug, codigo.'})

        try:
            total = Decimal(str(request.query_params.get('total', '0')))
        except Exception:
            total = Decimal('0')

        try:
            vendor = Vendor.objects.get(slug=vendor_slug)
        except Vendor.DoesNotExist:
            return Response({'valido': False, 'error': 'Tienda no encontrada.'})

        try:
            cupon = Cupon.objects.get(codigo=codigo, vendor=vendor, activo=True)
        except Cupon.DoesNotExist:
            return Response({'valido': False, 'error': 'Cupón inválido o inactivo.'})

        if cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos:
            return Response({'valido': False, 'error': 'Cupón agotado.'})
        if cupon.fecha_vencimiento and cupon.fecha_vencimiento < date.today():
            return Response({'valido': False, 'error': 'Cupón vencido.'})
        if not cupon.aplica_live:
            return Response({'valido': False, 'error': 'Cupón no aplica para compras en vivo.'})

        if cupon.tipo == 'porcentaje':
            descuento = (total * cupon.valor / 100).quantize(Decimal('0.01'))
        else:
            descuento = min(cupon.valor, total)

        return Response({
            'valido': True,
            'descuento_aplicado': str(descuento),
            'tipo': cupon.tipo,
            'valor': str(cupon.valor),
        })


# ─── Movimientos de Caja unificados ──────────────────────────────────────────

class MovimientosCajaView(APIView):
    """
    GET /api/v1/pos/movimientos/?period=today|week|month|year&page=1&page_size=20
    Returns a unified paginated chronological list of all cash events:
    - TurnoCaja apertura
    - VentaPOS completadas (ingreso de venta)
    - MovimientoCaja ingresos / retiros manuales
    - TurnoCaja cierre
    """
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def get(self, request):
        vendor = _vendor_or_403(request.user)
        period = request.query_params.get('period', 'today')
        page      = max(int(request.query_params.get('page', 1)), 1)
        page_size = min(int(request.query_params.get('page_size', 10)), 10000)

        today = timezone.localdate()

        if period == 'today':
            date_filter = {'date': today}
        elif period == 'week':
            date_filter = {'gte': today - timedelta(days=7)}
        elif period == 'month':
            date_filter = {'gte': today.replace(day=1)}
        elif period == 'year':
            date_filter = {'year': today.year}
        else:
            date_filter = {'date': today}

        def apply_date(qs, field):
            if 'date' in date_filter:
                return qs.filter(**{f'{field}__date': date_filter['date']})
            if 'gte' in date_filter:
                return qs.filter(**{f'{field}__date__gte': date_filter['gte']})
            if 'year' in date_filter:
                return qs.filter(**{f'{field}__year': date_filter['year']})
            return qs

        rows = []

        # ── Aperturas de turno ────────────────────────────────────────────────
        turnos = apply_date(
            TurnoCaja.objects.filter(caja__sucursal__vendor=vendor).select_related('caja', 'usuario'),
            'fecha_apertura',
        )
        for t in turnos:
            usuario = t.usuario.get_full_name() if t.usuario else '—'
            if not usuario.strip():
                usuario = getattr(t.usuario, 'email', '—') if t.usuario else '—'
            rows.append({
                'fecha': t.fecha_apertura.isoformat(),
                'caja': str(t.caja),
                'tipo': 'apertura',
                'usuario': usuario,
                'detalle': 'Apertura de caja',
                'monto': str(t.monto_apertura),
            })
            # ── Cierre del mismo turno ────────────────────────────────────────
            if t.fecha_cierre and t.monto_cierre is not None:
                rows.append({
                    'fecha': t.fecha_cierre.isoformat(),
                    'caja': str(t.caja),
                    'tipo': 'Cierre de caja',
                    'usuario': usuario,
                    'detalle': f'Cierre de caja con ID: {t.id}',
                    'monto': str(t.monto_cierre),
                })

        # ── Ventas POS completadas ─────────────────────────────────────────────
        ventas = apply_date(
            VentaPOS.objects.filter(vendor=vendor, status='completada')
                            .select_related('caja', 'metodo_pago', 'usuario'),
            'created_at',
        )
        for v in ventas:
            usuario = v.usuario.get_full_name() if v.usuario else '—'
            if not usuario.strip():
                usuario = getattr(v.usuario, 'email', '—') if v.usuario else '—'
            metodo = v.metodo_pago.nombre if v.metodo_pago else 'Sin método'
            vuelto = v.vuelto or Decimal('0')
            rows.append({
                'fecha': v.created_at.isoformat(),
                'caja': str(v.caja) if v.caja else '—',
                'tipo': 'INGRESOVENTA',
                'usuario': usuario,
                'detalle': f'Pago de venta con {metodo}: {v.total} – Vuelto: {vuelto}',
                'monto': str(v.total),
            })

        # ── Movimientos manuales (ingresos y retiros) ─────────────────────────
        movimientos = apply_date(
            MovimientoCaja.objects.filter(turno__caja__sucursal__vendor=vendor)
                                  .select_related('turno__caja', 'usuario'),
            'created_at',
        )
        for m in movimientos:
            usuario = m.usuario.get_full_name() if m.usuario else '—'
            if not usuario.strip():
                usuario = getattr(m.usuario, 'email', '—') if m.usuario else '—'
            tipo = 'INGRESO' if m.tipo == 'ingreso' else 'EGRESO'
            rows.append({
                'fecha': m.created_at.isoformat(),
                'caja': str(m.turno.caja) if m.turno and m.turno.caja else '—',
                'tipo': tipo,
                'usuario': usuario,
                'detalle': m.concepto,
                'monto': str(m.monto),
            })

        # ── Ordenar por fecha desc ─────────────────────────────────────────────
        rows.sort(key=lambda r: r['fecha'], reverse=True)

        total = len(rows)
        start = (page - 1) * page_size
        end   = start + page_size
        page_rows = rows[start:end]

        return Response({
            'count':    total,
            'page':     page,
            'pages':    (total + page_size - 1) // page_size if total else 1,
            'results':  page_rows,
        })
