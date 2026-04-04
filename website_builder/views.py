from decimal import Decimal

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q

from vendors.models import Vendor
from products.models import Product, ProductVariant, Category
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user
from .models import CartOrder, CartOrderItem
from .serializers import (
    PublicStoreSerializer,
    PublicProductSerializer,
    PublicCategorySerializer,
    CartOrderCreateSerializer,
    CartOrderDetailSerializer,
)


class PublicPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 48


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

    def get_queryset(self):
        vendor = get_object_or_404(Vendor, slug=self.kwargs['vendor_slug'])
        qs = (
            Product.objects
            .filter(vendor=vendor, is_active=True)
            .select_related('category')
            .prefetch_related('images', 'variant_objects')
        )

        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')

        if category:
            qs = qs.filter(category__id=category)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
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
            .select_related('category')
            .prefetch_related('images', 'variant_objects'),
            pk=self.kwargs['pk'],
            vendor=vendor,
            is_active=True,
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
            .filter(products__vendor=vendor, products__is_active=True)
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

                if variant_id:
                    try:
                        variant = ProductVariant.objects.get(
                            pk=variant_id,
                            product=product,
                            is_active=True,
                        )
                        available = variant.stock_extra if variant.stock_extra > 0 else product.stock
                    except ProductVariant.DoesNotExist:
                        errors.append(f"Ítem {i + 1}: variante {variant_id} no encontrada.")
                        continue
                else:
                    available = product.stock

                qty = item_data['quantity']
                if available < qty:
                    errors.append(
                        f"Stock insuficiente para '{product.name}'. "
                        f"Disponible: {available}, solicitado: {qty}."
                    )
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

            total = sum(item['subtotal'] for item in resolved)

            # ── Create CartOrder ──────────────────────────────────────────
            order = CartOrder.objects.create(
                vendor=vendor,
                customer_name=data['customer_name'],
                customer_phone=data['customer_phone'],
                customer_email=data['customer_email'],
                customer_address=data['customer_address'],
                delivery_method=data['delivery_method'],
                payment_method=data['payment_method'],
                notes=data['notes'],
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
                if item['variant'] and item['variant'].stock_extra > 0:
                    item['variant'].stock_extra -= item['quantity']
                    item['variant'].save(update_fields=['stock_extra'])
                else:
                    item['product'].stock -= item['quantity']
                    item['product'].save(update_fields=['stock'])

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

        return Response(CartOrderDetailSerializer(order, context={'request': request}).data)


class VendorCartOrderListView(ListAPIView):
    """GET /api/website-builder/orders/ — lista de pedidos de la tienda."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = CartOrderDetailSerializer
    pagination_class = PublicPagination

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        return CartOrder.objects.filter(vendor=vendor).prefetch_related('items__product').order_by('-created_at')


class VendorCartOrderDetailView(RetrieveAPIView):
    """GET /api/website-builder/orders/{pk}/ — detalle de pedido de la tienda."""
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    serializer_class = CartOrderDetailSerializer

    def get_object(self):
        vendor = get_vendor_for_user(self.request.user)
        return get_object_or_404(CartOrder.objects.prefetch_related('items__product'), pk=self.kwargs['pk'], vendor=vendor)
