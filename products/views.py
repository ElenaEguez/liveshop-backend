import json
from decimal import Decimal, InvalidOperation
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from .models import Category, Product, ProductImage, Inventory, ProductVariant
from .serializers import CategorySerializer, ProductSerializer, InventorySerializer, ProductVariantSerializer


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
    ordering = ['-created_at']

    def get_queryset(self):
        qs = Product.objects.filter(vendor=self.request.user.vendor_profile)

        # Manual search to support both ?search= and ?q=
        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search) |
                Q(internal_code__icontains=search) |
                Q(barcode__icontains=search)
            )

        # Category filter
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category_id=category)

        # Active status filter
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ('true', '1', 'yes'))

        # Filter by variant talla
        talla = self.request.query_params.get('talla')
        if talla:
            qs = qs.filter(variant_objects__talla__iexact=talla, variant_objects__is_active=True).distinct()

        # Filter by variant color
        color = self.request.query_params.get('color')
        if color:
            qs = qs.filter(variant_objects__color__icontains=color, variant_objects__is_active=True).distinct()

        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def _parse_variants(self, request):
        variants_raw = request.data.get('variants', '[]')
        if isinstance(variants_raw, str):
            try:
                return json.loads(variants_raw)
            except (json.JSONDecodeError, TypeError):
                return []
        return variants_raw if isinstance(variants_raw, list) else []

    def _save_images(self, product, request):
        for image in request.FILES.getlist('images'):
            ProductImage.objects.create(product=product, image=image)

    def _parse_purchase_cost(self, request):
        raw = request.data.get('purchase_cost', None)
        if raw is None or raw == '' or raw == 'null':
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    def _parse_decimal_field(self, request, field_name):
        raw = request.data.get(field_name, None)
        if raw is None or raw == '' or raw == 'null':
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    def perform_create(self, serializer):
        variants = self._parse_variants(self.request)
        shipping_cost = self._parse_decimal_field(self.request, 'shipping_cost')
        product = serializer.save(
            vendor=self.request.user.vendor_profile,
            variants=variants,
            shipping_cost=shipping_cost,
        )
        self._save_images(product, self.request)
        purchase_cost = self._parse_purchase_cost(self.request)
        Inventory.objects.get_or_create(
            product=product,
            defaults={'quantity': product.stock, 'purchase_cost': purchase_cost}
        )

    def perform_update(self, serializer):
        variants = self._parse_variants(self.request)
        shipping_cost = self._parse_decimal_field(self.request, 'shipping_cost')
        product = serializer.save(variants=variants, shipping_cost=shipping_cost)
        self._save_images(product, self.request)
        if 'purchase_cost' in self.request.data:
            purchase_cost = self._parse_purchase_cost(self.request)
            inventory, _ = Inventory.objects.get_or_create(
                product=product,
                defaults={'quantity': product.stock}
            )
            inventory.purchase_cost = purchase_cost
            inventory.save(update_fields=['purchase_cost'])

    @action(detail=True, methods=['get'], url_path='variantes')
    def variantes(self, request, pk=None):
        product = self.get_object()
        variants = ProductVariant.objects.filter(
            product=product, is_active=True
        ).order_by('talla', 'color')
        serializer = ProductVariantSerializer(variants, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='variantes/tallas')
    def variantes_tallas(self, request, pk=None):
        product = self.get_object()
        tallas = (
            ProductVariant.objects.filter(product=product, is_active=True)
            .exclude(talla='')
            .values_list('talla', flat=True)
            .distinct()
            .order_by('talla')
        )
        return Response({'tallas': list(tallas)})

    @action(detail=True, methods=['get'], url_path='variantes/colores')
    def variantes_colores(self, request, pk=None):
        product = self.get_object()
        talla = request.query_params.get('talla')
        qs = ProductVariant.objects.filter(product=product, is_active=True)
        if talla:
            qs = qs.filter(talla=talla)
        colores = qs.exclude(color='').order_by('color').values(
            'id', 'color', 'color_hex', 'stock_extra'
        )
        return Response({'colores': list(colores)})

    @action(detail=False, methods=['get'], url_path='variant-options')
    def variant_options(self, request):
        """Return all distinct tallas and colors across vendor's products."""
        vendor = request.user.vendor_profile
        qs = ProductVariant.objects.filter(
            product__vendor=vendor, is_active=True
        )
        tallas = list(
            qs.exclude(talla='').values_list('talla', flat=True).distinct().order_by('talla')
        )
        colors = list(
            qs.exclude(color='').values_list('color', flat=True).distinct().order_by('color')
        )
        return Response({'tallas': tallas, 'colors': colors})


class InventoryViewSet(viewsets.ModelViewSet):
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        qs = Inventory.objects.filter(product__vendor=self.request.user.vendor_profile)
        almacen_id = self.request.query_params.get('almacen_id')
        category_id = self.request.query_params.get('category')
        search = self.request.query_params.get('search', '').strip()
        talla = self.request.query_params.get('talla', '').strip()
        color = self.request.query_params.get('color', '').strip()

        if almacen_id:
            qs = qs.filter(almacen_id=almacen_id)
        if category_id:
            qs = qs.filter(product__category_id=category_id)
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search) |
                Q(product__internal_code__icontains=search) |
                Q(product__barcode__icontains=search)
            )
        if talla:
            qs = qs.filter(
                product__variant_objects__talla__iexact=talla,
                product__variant_objects__is_active=True
            ).distinct()
        if color:
            qs = qs.filter(
                product__variant_objects__color__icontains=color,
                product__variant_objects__is_active=True
            ).distinct()
        return qs
