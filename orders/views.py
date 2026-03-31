from decimal import Decimal

from django.db.models import (
    Count, DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum,
)
from django.db.models.functions import TruncDay, TruncHour, TruncMonth, TruncWeek
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Reservation
from .serializers import ReservationSerializer, PublicReservationSerializer
from products.models import Inventory, ProductVariant
from payments.models import Cupon
from livestreams.models import LiveSession
from vendors.permissions import (
    IsVendorOrTeamMember,
    get_vendor_for_user,
    get_role_for_user,
)


# ─── Pagination ─────────────────────────────────────────────────────────────

class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


# ─── Constants ──────────────────────────────────────────────────────────────

CONFIRMED_STATUSES = ['confirmed', 'paid', 'shipped', 'recibido']

MONTH_NAMES_ES = [
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]
MONTH_ABBR_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
                  'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
DAY_NAMES_ES = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']


# ─── Public view ────────────────────────────────────────────────────────────

class PublicReservationCreateView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicReservationSerializer

    def get_live_session(self):
        slug = self.kwargs.get('slug')
        return get_object_or_404(LiveSession, slug=slug, status='live')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['live_session'] = self.get_live_session()
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data['product']
        quantity = serializer.validated_data['quantity']
        variant_id = serializer.validated_data.pop('variant_id', None)
        cupon_codigo = serializer.validated_data.pop('cupon_codigo', '')

        # ── Validate and apply coupon ──────────────────────────────────────────
        cupon = None
        descuento = Decimal('0')
        if cupon_codigo:
            live_session = self.get_live_session()
            try:
                cupon = Cupon.objects.get(
                    codigo=cupon_codigo, vendor=live_session.vendor, activo=True, aplica_live=True)
                if not (cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos):
                    subtotal = product.price * quantity
                    if cupon.tipo == 'porcentaje':
                        descuento = (subtotal * cupon.valor / 100).quantize(Decimal('0.01'))
                    else:
                        descuento = min(cupon.valor, subtotal)
            except Cupon.DoesNotExist:
                pass

        # ── Validate stock ────────────────────────────────────────────────────
        # Check main inventory
        try:
            inventory = Inventory.objects.get(product=product, is_active=True)
            available = inventory.available_quantity
            if available < quantity:
                return Response(
                    {'error': 'Stock insuficiente', 'disponible': available},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Inventory.DoesNotExist:
            return Response(
                {'error': 'Stock insuficiente', 'disponible': 0},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check variant stock if applicable
        variant = None
        if variant_id:
            try:
                variant = ProductVariant.objects.get(id=variant_id, product=product)
                if variant.stock_extra > 0 and variant.stock_extra < quantity:
                    return Response(
                        {'error': f'Stock insuficiente para esta variante. Disponible: {variant.stock_extra}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except ProductVariant.DoesNotExist:
                pass

        self.perform_create(serializer, cupon=cupon, descuento=descuento, variant=variant)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer, cupon=None, descuento=None, variant=None):
        from django.db import transaction

        live_session = self.get_live_session()
        product = serializer.validated_data['product']
        quantity = serializer.validated_data['quantity']

        with transaction.atomic():
            reservation = serializer.save(
                session=live_session,
                cupon=cupon,
                descuento=descuento or Decimal('0'),
                variant=variant,
            )
            # Update coupon usage
            if cupon:
                Cupon.objects.filter(pk=cupon.pk).update(
                    usos_actuales=F('usos_actuales') + 1
                )
            # Atomic stock reservation
            Inventory.objects.filter(product=product, is_active=True).update(
                reserved_quantity=F('reserved_quantity') + quantity
            )
            # Decrement variant stock if it has its own stock
            if variant and variant.stock_extra > 0:
                ProductVariant.objects.filter(pk=variant.pk).update(
                    stock_extra=F('stock_extra') - quantity
                )

        channel_layer = get_channel_layer()

        payload = {
            "reservation_id": reservation.id,
            "customer_name": reservation.customer_name,
            "product_id": product.id,
            "product_name": product.name,
            "quantity": quantity,
            "total_price": float(reservation.total_price),
            "status": reservation.status,
            "created_at": reservation.created_at.isoformat(),
        }

        async_to_sync(channel_layer.group_send)(
            f"session_{live_session.id}",
            {"type": "reservation_update", **payload},
        )

        try:
            async_to_sync(channel_layer.group_send)(
                f"vendor_{live_session.vendor_id}",
                {"type": "vendor_update", "event_type": "new_order", "data": payload},
            )
        except Exception:
            pass


# ─── ReservationViewSet ──────────────────────────────────────────────────────

class ReservationViewSet(viewsets.ModelViewSet):
    serializer_class = ReservationSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    pagination_class = StandardPagination

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_vendor(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise PermissionDenied("Sin perfil de vendedor asociado.")
        return vendor

    def _check_write_permission(self):
        """
        Roles:
          vendor_owner / admin → full write access
          assistant            → only PATCH (status updates)
          payments             → read-only on reservations
        """
        role = get_role_for_user(self.request.user)
        if role in ('vendor_owner', 'admin'):
            return
        if role == 'assistant' and self.request.method == 'PATCH':
            return
        raise PermissionDenied("Tu rol no permite esta acción en reservaciones.")

    # ── queryset ─────────────────────────────────────────────────────────────

    def get_queryset(self):
        vendor = self._get_vendor()
        qs = Reservation.objects.filter(
            session__vendor=vendor
        ).select_related('product', 'session').order_by('-created_at')

        session_id = self.request.query_params.get('session')
        status_filter = self.request.query_params.get('status')
        search = self.request.query_params.get('search')

        if session_id:
            qs = qs.filter(session_id=session_id)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(customer_name__icontains=search)

        return qs

    # ── write methods ─────────────────────────────────────────────────────────

    def create(self, request, *args, **kwargs):
        self._check_write_permission()
        from django.db import transaction
        with transaction.atomic():
            response = super().create(request, *args, **kwargs)
            reservation = Reservation.objects.get(pk=response.data['id'])

            try:
                inventory = Inventory.objects.get(product=reservation.product, is_active=True)
                if inventory.available_quantity >= reservation.quantity:
                    inventory.reserved_quantity += reservation.quantity
                    inventory.save()
                else:
                    reservation.delete()
                    return Response(
                        {'error': 'No hay suficiente stock disponible.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Inventory.DoesNotExist:
                reservation.delete()
                return Response(
                    {'error': 'No hay inventario disponible para este producto.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            return response

    def update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().destroy(request, *args, **kwargs)


# ─── OrdersDashboardView ─────────────────────────────────────────────────────

class OrdersDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def get(self, request):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response({'error': 'Sin perfil de vendedor asociado.'}, status=403)

        # ── Parse params ────────────────────────────────────────────────────
        now = timezone.now()
        period = request.query_params.get('period', 'month')
        year = int(request.query_params.get('year', now.year))
        month = int(request.query_params.get('month', now.month))
        category_id = request.query_params.get('category_id')

        # ── Date filter ─────────────────────────────────────────────────────
        import datetime
        if period == 'day':
            date_str = request.query_params.get('date')
            if date_str:
                day_date = datetime.date.fromisoformat(date_str)
            else:
                day_date = now.date()
            date_filter = {'created_at__date': day_date}
            gasto_date_filter = {'fecha': day_date}
            period_label = day_date.strftime('%d/%m/%Y')
            trunc_fn = TruncHour

        elif period == 'year':
            date_filter = {'created_at__year': year}
            gasto_date_filter = {'fecha__year': year}
            period_label = str(year)
            trunc_fn = TruncMonth

        elif period == 'week':
            today = now.date()
            week_start = today - datetime.timedelta(days=today.weekday())
            week_end = week_start + datetime.timedelta(days=7)
            date_filter = {
                'created_at__date__gte': week_start,
                'created_at__date__lt': week_end,
            }
            gasto_date_filter = {
                'fecha__gte': week_start,
                'fecha__lt': week_end,
            }
            period_label = (
                f"{week_start.strftime('%d/%m')} - "
                f"{(week_end - datetime.timedelta(days=1)).strftime('%d/%m/%Y')}"
            )
            trunc_fn = TruncDay

        else:  # month (default)
            date_filter = {'created_at__year': year, 'created_at__month': month}
            gasto_date_filter = {'fecha__year': year, 'fecha__month': month}
            period_label = f"{MONTH_NAMES_ES[month - 1]} {year}"
            trunc_fn = TruncWeek

        # ── Base queryset ───────────────────────────────────────────────────
        qs = Reservation.objects.filter(
            product__vendor=vendor,
            status__in=CONFIRMED_STATUSES,
            **date_filter
        ).select_related('product', 'product__category')

        if category_id:
            qs = qs.filter(product__category_id=category_id)

        # ── Revenue expression ──────────────────────────────────────────────
        revenue_expr = ExpressionWrapper(
            F('product__price') * F('quantity'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )

        # ── Cost subquery (active inventory purchase_cost) ──────────────────
        cost_sq = Inventory.objects.filter(
            product=OuterRef('product'), is_active=True
        ).values('purchase_cost')[:1]

        qs_with_cost = qs.annotate(
            unit_cost=Subquery(cost_sq, output_field=DecimalField(max_digits=10, decimal_places=2))
        )

        cost_expr = ExpressionWrapper(
            F('unit_cost') * F('quantity'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )

        # ── Totals ──────────────────────────────────────────────────────────
        totals = qs_with_cost.aggregate(
            total_orders=Count('id'),
            total_revenue=Sum(revenue_expr),
            total_cost=Sum(cost_expr),
        )
        total_orders = totals['total_orders'] or 0
        total_revenue = totals['total_revenue'] or Decimal('0')
        total_cost = totals['total_cost']  # None if all purchase_costs are NULL
        gross_margin = (total_revenue - total_cost) if total_cost is not None else None

        # ── Gastos operativos ────────────────────────────────────────────────
        from payments.models import GastoOperativo
        gastos_qs = GastoOperativo.objects.filter(
            vendor=vendor,
            status='activo',
            **gasto_date_filter
        )
        gastos_totals = gastos_qs.aggregate(total=Sum('monto'))
        total_gastos_operativos = gastos_totals['total'] or Decimal('0')
        utilidad_neta = (gross_margin - total_gastos_operativos) if gross_margin is not None else None

        gastos_por_categoria = list(
            gastos_qs.values('categoria__nombre').annotate(
                total=Sum('monto')
            ).order_by('-total').values_list('categoria__nombre', 'total')
        )
        gastos_por_categoria = [
            {'categoria': nombre or 'Sin categoría', 'total': str(total)}
            for nombre, total in gastos_por_categoria
        ]

        # ── Pending payment confirmation ────────────────────────────────────
        pending_payment_confirmation = Reservation.objects.filter(
            product__vendor=vendor,
            payment__status='submitted',
        ).count()

        # ── Variant breakdown (for sales_by_product) ────────────────────────
        variant_rows = qs.exclude(variant_detail='').values(
            'product__id', 'variant_detail',
        ).annotate(
            units_sold=Sum('quantity'),
        ).order_by('product__id', 'variant_detail')

        variant_by_product = {}
        for vr in variant_rows:
            pid = vr['product__id']
            variant_by_product.setdefault(pid, []).append({
                'variante': vr['variant_detail'],
                'units_sold': vr['units_sold'],
            })

        # ── Sales by product ────────────────────────────────────────────────
        product_rows = qs_with_cost.values(
            'product__id', 'product__name', 'product__category__name'
        ).annotate(
            units_sold=Sum('quantity'),
            revenue=Sum(revenue_expr),
            cost=Sum(cost_expr),
        ).order_by('-revenue')

        sales_by_product = []
        for row in product_rows:
            item = {
                'product_id': row['product__id'],
                'product_name': row['product__name'],
                'category': row['product__category__name'] or '',
                'units_sold': row['units_sold'],
                'revenue': str(row['revenue'] or Decimal('0')),
            }
            if row['cost'] is not None:
                item['cost'] = str(row['cost'])
                item['margin'] = str((row['revenue'] or Decimal('0')) - row['cost'])
            item['variantes'] = variant_by_product.get(row['product__id'], [])
            sales_by_product.append(item)

        # ── Sales by period (for charts) ────────────────────────────────────
        period_rows = qs.annotate(
            period=trunc_fn('created_at')
        ).values('period').annotate(
            revenue=Sum(revenue_expr),
            orders=Count('id'),
        ).order_by('period')

        sales_by_period = []
        for i, row in enumerate(period_rows, 1):
            if period == 'day':
                label = row['period'].strftime('%H:00')
            elif period == 'month':
                label = f"Semana {i}"
            elif period == 'week':
                label = DAY_NAMES_ES[row['period'].weekday()]
            else:  # year
                label = MONTH_ABBR_ES[row['period'].month - 1]

            sales_by_period.append({
                'label': label,
                'revenue': str(row['revenue'] or Decimal('0')),
                'orders': row['orders'],
            })

        # ── POS (ventas físicas) ─────────────────────────────────────────────
        from payments.models import VentaPOS
        pos_date_filter = {}
        if period == 'day':
            pos_date_filter['created_at__date'] = day_date
        elif period == 'year':
            pos_date_filter['created_at__year'] = year
        elif period == 'week':
            pos_date_filter['created_at__date__gte'] = week_start
            pos_date_filter['created_at__date__lt'] = week_end
        else:
            pos_date_filter['created_at__year'] = year
            pos_date_filter['created_at__month'] = month

        pos_qs = VentaPOS.objects.filter(
            vendor=vendor,
            status='completada',
            **pos_date_filter
        )
        pos_totals = pos_qs.aggregate(
            total_orders=Count('id'),
            total_revenue=Sum('total'),
        )
        pos_total_orders = pos_totals['total_orders'] or 0
        pos_total_revenue = pos_totals['total_revenue'] or Decimal('0')

        # ── Flujo de caja (MovimientoCaja) ──────────────────────────────────
        from vendors.models import MovimientoCaja, TurnoCaja
        # Reuse same period bounds already computed for pos_date_filter
        turno_date_filter = {}
        if period == 'day':
            turno_date_filter['fecha_apertura__date'] = day_date
        elif period == 'year':
            turno_date_filter['fecha_apertura__year'] = year
        elif period == 'week':
            turno_date_filter['fecha_apertura__date__gte'] = week_start
            turno_date_filter['fecha_apertura__date__lt'] = week_end
        else:
            turno_date_filter['fecha_apertura__year'] = year
            turno_date_filter['fecha_apertura__month'] = month

        turnos_del_periodo = TurnoCaja.objects.filter(
            caja__sucursal__vendor=vendor,
            **turno_date_filter,
        )
        mov_agg = MovimientoCaja.objects.filter(
            turno__in=turnos_del_periodo,
        ).values('tipo').annotate(total=Sum('monto'))
        total_ingresos_caja = Decimal('0')
        total_retiros_caja = Decimal('0')
        for row in mov_agg:
            if row['tipo'] == 'ingreso':
                total_ingresos_caja = row['total'] or Decimal('0')
            elif row['tipo'] == 'retiro':
                total_retiros_caja = row['total'] or Decimal('0')

        # ── Response ────────────────────────────────────────────────────────
        response_data = {
            'period_label': period_label,
            'total_orders': total_orders,
            'total_revenue': str(total_revenue),
            'pos_total_orders': pos_total_orders,
            'pos_total_revenue': str(pos_total_revenue),
            'pending_payment_confirmation': pending_payment_confirmation,
            'sales_by_product': sales_by_product,
            'sales_by_period': sales_by_period,
        }
        if total_cost is not None:
            response_data['total_cost'] = str(total_cost)
            response_data['gross_margin'] = str(gross_margin)
        else:
            response_data['total_cost'] = None
            response_data['gross_margin'] = None

        response_data['total_gastos_operativos'] = str(total_gastos_operativos)
        response_data['utilidad_neta'] = str(utilidad_neta) if utilidad_neta is not None else None
        response_data['gastos_por_categoria'] = gastos_por_categoria
        response_data['total_ingresos_caja'] = str(total_ingresos_caja)
        response_data['total_retiros_caja'] = str(total_retiros_caja)

        return Response(response_data)
