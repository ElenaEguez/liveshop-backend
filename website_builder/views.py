from decimal import Decimal
from datetime import date

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.filters import OrderingFilter
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q, Prefetch
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from vendors.models import Vendor
from products.models import Product, ProductVariant, Category
from products.stock_service import (
    StockError,
    apply_stock_delta,
    check_available_for_sale,
    get_primary_inventory,
    product_has_variants,
)
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user
from payments.models import Cupon
from .models import CartOrder, CartOrderItem
from .serializers import (
    PublicStoreSerializer,
    PublicProductSerializer,
    PublicCategorySerializer,
    CartOrderCreateSerializer,
    CartOrderDetailSerializer,
)


def _emit_vendor_update(vendor_id, event_type, data):
    """Send a real-time event to the vendor's WebSocket group."""
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'vendor_{vendor_id}',
            {'type': 'vendor_update', 'event_type': event_type, 'data': data},
        )
    except Exception:
        pass


class PublicPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 48


def _category_branch_ids(root_pk):
    """IDs de la categoría raíz y todas sus subcategorías (árbol completo)."""
    ids = [root_pk]
    frontier = [root_pk]
    while frontier:
        children = list(
            Category.objects.filter(parent_id__in=frontier).values_list('pk', flat=True)
        )
        ids.extend(children)
        frontier = children
    return ids


class PublicStoreView(APIView):
    """GET /api/public/{vendor_slug}/ — información pública de la tienda."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, vendor_slug):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)
        serializer = PublicStoreSerializer(vendor, context={'request': request})
        return Response(serializer.data)


class PublicCatalogView(ListAPIView):
    """GET /api/public/{vendor_slug}/products/ — catálogo con filtros y paginación."""
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicProductSerializer
    pagination_class = PublicPagination
    filter_backends = [OrderingFilter]
    ordering_fields = ['price', 'created_at', 'id', 'name']
    ordering = ['-created_at']

    def get_queryset(self):
        vendor = get_object_or_404(Vendor, slug=self.kwargs['vendor_slug'])
        qs = (
            Product.objects
            .filter(vendor=vendor, is_active=True, is_active_web=True)
            .select_related('category', 'vendor')
            .prefetch_related(
                'images',
                Prefetch(
                    'variant_objects',
                    queryset=ProductVariant.objects.filter(is_active=True),
                ),
            )
        )

        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')

        if category:
            try:
                root_id = int(category)
            except (ValueError, TypeError):
                root_id = None
            if root_id is not None:
                branch_ids = _category_branch_ids(root_id)
                qs = qs.filter(category_id__in=branch_ids)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        if self.request.query_params.get('bestseller') in ('1', 'true', 'yes'):
            qs = qs.filter(web_is_bestseller=True)
        if self.request.query_params.get('new') in ('1', 'true', 'yes'):
            qs = qs.filter(web_is_new=True)

        return qs


class PublicProductDetailView(RetrieveAPIView):
    """GET /api/public/{vendor_slug}/products/{id}/ — detalle de producto."""
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicProductSerializer

    def get_object(self):
        vendor = get_object_or_404(Vendor, slug=self.kwargs['vendor_slug'])
        return get_object_or_404(
            Product.objects
            .select_related('category', 'vendor')
            .prefetch_related(
                'images',
                Prefetch(
                    'variant_objects',
                    queryset=ProductVariant.objects.filter(is_active=True),
                ),
            ),
            pk=self.kwargs['pk'],
            vendor=vendor,
            is_active=True,
            is_active_web=True,
        )


class PublicCategoriesView(ListAPIView):
    """GET /api/public/{vendor_slug}/categories/ — categorías con productos activos."""
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PublicCategorySerializer

    def get_queryset(self):
        vendor = get_object_or_404(Vendor, slug=self.kwargs['vendor_slug'])
        return (
            Category.objects
            .filter(products__vendor=vendor, products__is_active=True, products__is_active_web=True)
            .distinct()
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['vendor_slug'] = self.kwargs['vendor_slug']
        return ctx


# ── Checkout views ─────────────────────────────────────────────────────────────

class PublicCheckoutView(APIView):
    """POST /api/public/{vendor_slug}/checkout/ — crear pedido sin login."""
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [JSONParser]

    def post(self, request, vendor_slug):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)

        serializer = CartOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            # ── Validate stock and resolve each item ──────────────────────
            errors = []
            resolved = []

            for i, item_data in enumerate(data['items']):
                try:
                    product = Product.objects.get(
                        pk=item_data['product_id'],
                        vendor=vendor,
                        is_active=True,
                    )
                except Product.DoesNotExist:
                    errors.append(f"Ítem {i + 1}: producto {item_data['product_id']} no encontrado.")
                    continue

                variant = None
                variant_id = item_data.get('variant_id')
                qty = item_data['quantity']

                if product_has_variants(product.id) and not variant_id:
                    errors.append(
                        f"Ítem {i + 1}: '{product.name}' tiene variantes. "
                        f"Seleccione talla/color."
                    )
                    continue

                if variant_id:
                    try:
                        variant = ProductVariant.objects.get(
                            pk=variant_id,
                            product=product,
                            is_active=True,
                        )
                    except ProductVariant.DoesNotExist:
                        errors.append(f"Ítem {i + 1}: variante {variant_id} no encontrada.")
                        continue

                try:
                    check_available_for_sale(product.id, qty, variant_id)
                except StockError as exc:
                    errors.append(f"Ítem {i + 1}: {exc}")
                    continue

                unit_price = product.price
                resolved.append({
                    'product': product,
                    'variant': variant,
                    'variant_id': variant_id,
                    'quantity': qty,
                    'unit_price': unit_price,
                    'subtotal': unit_price * qty,
                })

            if errors:
                return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

            subtotal = sum(item['subtotal'] for item in resolved)
            cupon_codigo = (data.get('cupon_codigo') or '').strip()
            descuento_cupon = Decimal('0')
            cupon = None

            if cupon_codigo:
                try:
                    cupon = Cupon.objects.get(codigo=cupon_codigo, vendor=vendor, activo=True)
                except Cupon.DoesNotExist:
                    return Response({'error': 'Cupón inválido o inactivo.'}, status=status.HTTP_400_BAD_REQUEST)

                if cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos:
                    return Response({'error': 'Cupón agotado.'}, status=status.HTTP_400_BAD_REQUEST)
                if cupon.fecha_vencimiento and cupon.fecha_vencimiento < date.today():
                    return Response({'error': 'Cupón vencido.'}, status=status.HTTP_400_BAD_REQUEST)
                if not cupon.aplica_live:
                    return Response({'error': 'Cupón no aplica para compras web.'}, status=status.HTTP_400_BAD_REQUEST)

                if cupon.tipo == 'porcentaje':
                    descuento_cupon = (subtotal * cupon.valor / 100).quantize(Decimal('0.01'))
                else:
                    descuento_cupon = min(cupon.valor, subtotal)

            total = max(subtotal - descuento_cupon, Decimal('0'))

            # ── Create CartOrder ──────────────────────────────────────────
            order = CartOrder.objects.create(
                vendor=vendor,
                customer_name=data['customer_name'],
                customer_phone=data['customer_phone'],
                customer_email=data['customer_email'],
                customer_address=data['customer_address'],
                delivery_method=data['delivery_method'],
                payment_method=data['payment_method'],
                notes=(data['notes'] or '') + (f"\nCupón: {cupon_codigo} (-Bs {descuento_cupon})" if cupon_codigo else ''),
                total_amount=total,
            )

            # ── Create items and decrement stock ──────────────────────────
            for item in resolved:
                CartOrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    variant_id=item['variant_id'],
                    quantity=item['quantity'],
                    unit_price=item['unit_price'],
                    subtotal=item['subtotal'],
                )
                inv = get_primary_inventory(item['product'].id)
                if not inv or not inv.almacen:
                    raise StockError(
                        f"Sin almacén de inventario para '{item['product'].name}'."
                    )
                try:
                    apply_stock_delta(
                        product=item['product'],
                        almacen=inv.almacen,
                        delta=-int(item['quantity']),
                        variant=item['variant'],
                        motivo='venta_web',
                        documento_ref=f'WEB-{order.id}',
                        notas=f'Pedido web #{order.id}',
                    )
                except StockError as exc:
                    return Response({'errors': [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

            if cupon:
                cupon.usos_actuales += 1
                cupon.save(update_fields=['usos_actuales'])

        _emit_vendor_update(
            vendor.id,
            'web_order_created',
            {'order_id': order.id, 'status': order.status, 'total_amount': str(order.total_amount)},
        )
        return Response(
            CartOrderDetailSerializer(order, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class PublicOrderStatusView(APIView):
    """GET /api/public/{vendor_slug}/order/{pk}/ — estado del pedido sin auth."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, vendor_slug, pk):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)
        order = get_object_or_404(
            CartOrder.objects.prefetch_related('items__product'),
            pk=pk,
            vendor=vendor,
        )
        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class PublicReceiptUploadView(APIView):
    """POST /api/public/{vendor_slug}/order/{pk}/receipt/ — subir comprobante."""
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser]

    def post(self, request, vendor_slug, pk):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)
        order = get_object_or_404(CartOrder, pk=pk, vendor=vendor)

        if order.status in ('confirmed', 'cancelled', 'delivered'):
            return Response(
                {'error': 'No se puede actualizar el comprobante de un pedido en este estado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'receipt' not in request.FILES:
            return Response(
                {'error': 'Se requiere el campo "receipt" con el comprobante de pago.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.payment_receipt = request.FILES['receipt']
        order.status = 'pending_confirmation'
        order.save(update_fields=['payment_receipt', 'status'])
        _emit_vendor_update(
            vendor.id,
            'web_order_status_changed',
            {'order_id': order.id, 'status': order.status},
        )

        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class PublicOrderCancelView(APIView):
    """POST /api/public/{vendor_slug}/order/{pk}/cancel/ — cancelar pedido público."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, vendor_slug, pk):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)
        order = get_object_or_404(CartOrder.objects.prefetch_related('items__product'), pk=pk, vendor=vendor)

        if order.status in ('delivered', 'cancelled'):
            return Response(
                {'error': 'No se puede cancelar un pedido entregado o ya cancelado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for item in order.items.select_related('product').all():
                variant = None
                if item.variant_id:
                    try:
                        variant = ProductVariant.objects.get(pk=item.variant_id)
                    except ProductVariant.DoesNotExist:
                        pass
                inv = get_primary_inventory(item.product_id)
                if inv and inv.almacen:
                    apply_stock_delta(
                        product=item.product,
                        almacen=inv.almacen,
                        delta=int(item.quantity),
                        variant=variant,
                        motivo='devolucion',
                        documento_ref=f'WEB-CANCEL-{order.id}',
                        notas=f'Cancelación pedido web #{order.id}',
                    )

            order.status = 'cancelled'
            order.save(update_fields=['status'])

        _emit_vendor_update(
            vendor.id,
            'web_order_status_changed',
            {'order_id': order.id, 'status': order.status},
        )
        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class VendorCartOrderListView(ListAPIView):
    """GET /api/website-builder/orders/ — lista de pedidos de la tienda."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = CartOrderDetailSerializer
    pagination_class = PublicPagination

    def get_queryset(self):
        from django.db.models import Q
        vendor = get_vendor_for_user(self.request.user)
        qs = CartOrder.objects.filter(vendor=vendor).prefetch_related('items__product').order_by('-created_at')

        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(customer_name__icontains=search) |
                Q(customer_phone__icontains=search) |
                Q(id__icontains=search)
            )

        return qs


class VendorCartOrderDetailView(RetrieveAPIView):
    """GET /api/website-builder/orders/{pk}/ — detalle de pedido de la tienda."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = CartOrderDetailSerializer

    def get_object(self):
        vendor = get_vendor_for_user(self.request.user)
        return get_object_or_404(CartOrder.objects.prefetch_related('items__product'), pk=self.kwargs['pk'], vendor=vendor)


class VendorCartOrderConfirmView(APIView):
    """POST /api/website-builder/orders/{pk}/confirm/ — confirmar pedido."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def post(self, request, pk):
        vendor = get_vendor_for_user(request.user)
        order = get_object_or_404(CartOrder, pk=pk, vendor=vendor)

        if order.status not in ('pending', 'pending_confirmation'):
            return Response(
                {'error': 'Solo se pueden confirmar pedidos pendientes.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = 'confirmed'
        order.save(update_fields=['status'])
        _emit_vendor_update(
            vendor.id,
            'web_order_status_changed',
            {'order_id': order.id, 'status': order.status},
        )

        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class VendorCartOrderCancelView(APIView):
    """POST /api/website-builder/orders/{pk}/cancel/ — cancelar pedido."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def post(self, request, pk):
        vendor = get_vendor_for_user(request.user)
        order = get_object_or_404(CartOrder, pk=pk, vendor=vendor)

        if order.status in ('delivered', 'cancelled'):
            return Response(
                {'error': 'No se puede cancelar un pedido entregado o ya cancelado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for item in order.items.select_related('product').all():
                variant = None
                if item.variant_id:
                    try:
                        variant = ProductVariant.objects.get(pk=item.variant_id)
                    except ProductVariant.DoesNotExist:
                        pass
                inv = get_primary_inventory(item.product_id)
                if inv and inv.almacen:
                    apply_stock_delta(
                        product=item.product,
                        almacen=inv.almacen,
                        delta=int(item.quantity),
                        variant=variant,
                        motivo='devolucion',
                        documento_ref=f'WEB-CANCEL-{order.id}',
                        notas=f'Cancelación pedido web #{order.id} (vendedor)',
                    )

            order.status = 'cancelled'
            order.save(update_fields=['status'])

        _emit_vendor_update(
            vendor.id,
            'web_order_status_changed',
            {'order_id': order.id, 'status': order.status},
        )

        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class VendorCartOrderPendingCountView(APIView):
    """GET /api/website-builder/orders/pending-count/ — pedidos pendientes del vendedor."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def get(self, request):
        vendor = get_vendor_for_user(request.user)
        count = CartOrder.objects.filter(
            vendor=vendor,
            status__in=['pending', 'pending_confirmation']
        ).count()
        return Response({'count': count})


class VendorCartOrderMarkDeliveredView(APIView):
    """POST /api/website-builder/orders/{pk}/mark-delivered/ — marcar como entregado."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def post(self, request, pk):
        vendor = get_vendor_for_user(request.user)
        order = get_object_or_404(CartOrder, pk=pk, vendor=vendor)

        if order.status != 'confirmed':
            return Response(
                {'error': 'Solo se pueden marcar como entregados pedidos confirmados.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = 'delivered'
        order.save(update_fields=['status'])
        _emit_vendor_update(
            vendor.id,
            'web_order_status_changed',
            {'order_id': order.id, 'status': order.status},
        )

        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class VendorCartOrderDeleteView(APIView):
    """DELETE /api/website-builder/orders/{pk}/delete/ — eliminar pedido cancelado."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def delete(self, request, pk):
        vendor = get_vendor_for_user(request.user)
        order = get_object_or_404(CartOrder, pk=pk, vendor=vendor)

        if order.status != 'cancelled':
            return Response(
                {'error': 'Solo se pueden eliminar pedidos en estado cancelado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
