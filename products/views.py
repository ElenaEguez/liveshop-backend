import json
from decimal import Decimal, InvalidOperation
from rest_framework import generics, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.db.models import IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from .models import Category, Product, ProductImage, Inventory, ProductVariant
from vendors.permissions import get_vendor_for_user
from .serializers import (
    CategorySerializer, CategoryWithSubcategoriesSerializer,
    ProductSerializer, InventorySerializer, ProductVariantSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if vendor is None:
            return Category.objects.none()
        return Category.objects.filter(vendor=vendor)

    def perform_create(self, serializer):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise ValidationError({'vendor': 'Sin perfil de vendedor asociado.'})
        serializer.save(vendor=vendor)


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
    ordering = ['-created_at']

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return Product.objects.none()
        qs = Product.objects.filter(vendor=vendor)
        canal = (self.request.query_params.get('canal') or '').strip().lower()
        if canal == 'live':
            qs = qs.filter(is_active_live=True)
        elif canal in ('pos', 'tienda'):
            qs = qs.filter(is_active_pos=True)
        elif canal == 'web':
            qs = qs.filter(is_active_web=True)


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
        talla = (self.request.query_params.get('talla') or '').strip()
        if talla:
            qs = qs.filter(
                Q(variant_objects__talla__iexact=talla, variant_objects__is_active=True) |
                Q(variants__icontains=f'"size": "{talla}"') |
                Q(variants__icontains=f'"talla": "{talla}"')
            ).distinct()

        # Filter by variant color
        color = (self.request.query_params.get('color') or '').strip()
        if color:
            qs = qs.filter(
                Q(variant_objects__color__icontains=color, variant_objects__is_active=True) |
                Q(variants__icontains=f'"color": "{color}"')
            ).distinct()

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

    def _sync_variant_objects(self, product, variants):
        ProductVariant.objects.filter(product=product).delete()
        if not isinstance(variants, list):
            return

        for v in variants:
            if not isinstance(v, dict):
                continue
            talla = (v.get('size') or v.get('talla') or '').strip()
            color = (v.get('color') or '').strip()
            color_hex = (v.get('color_hex') or '').strip()
            stock = v.get('stock', 0)
            try:
                stock_int = int(stock)
            except (TypeError, ValueError):
                stock_int = 0
            ProductVariant.objects.create(
                product=product,
                talla=talla,
                color=color,
                color_hex=color_hex,
                stock_extra=max(stock_int, 0),
                is_active=True,
            )

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
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise ValidationError({'detail': 'Sin perfil de vendedor asociado.'})
        variants = self._parse_variants(self.request)
        shipping_cost = self._parse_decimal_field(self.request, 'shipping_cost')
        product = serializer.save(
            vendor=vendor,
            variants=variants,
            shipping_cost=shipping_cost,
        )
        self._save_images(product, self.request)
        self._sync_variant_objects(product, variants)
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
        self._sync_variant_objects(product, variants)
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
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response({'tallas': [], 'colors': []})

        # From ProductVariant model objects
        pv_qs = ProductVariant.objects.filter(
            product__vendor=vendor, is_active=True
        )
        tallas_set = set(
            pv_qs.exclude(talla='').values_list('talla', flat=True)
        )
        colors_set = set(
            pv_qs.exclude(color='').values_list('color', flat=True)
        )

        # Also extract from legacy JSONField `variants` on Product
        products_with_json = Product.objects.filter(vendor=vendor, is_active=True).exclude(variants=[])
        for product in products_with_json:
            if not isinstance(product.variants, list):
                continue
            for v in product.variants:
                if not isinstance(v, dict):
                    continue
                size = v.get('size') or v.get('talla') or ''
                color = v.get('color') or ''
                if size:
                    tallas_set.add(str(size).strip())
                if color:
                    colors_set.add(str(color).strip())

        tallas = sorted(t for t in tallas_set if t)
        colors = sorted(c for c in colors_set if c)
        return Response({'tallas': tallas, 'colors': colors})


class InventoryViewSet(viewsets.ModelViewSet):
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from payments.models import VentaPOSItem

        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return Inventory.objects.none()
        qs = Inventory.objects.filter(product__vendor=vendor)
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
                Q(product__variant_objects__talla__iexact=talla, product__variant_objects__is_active=True) |
                Q(product__variants__icontains=f'"size": "{talla}"') |
                Q(product__variants__icontains=f'"talla": "{talla}"')
            ).distinct()
        if color:
            qs = qs.filter(
                Q(product__variant_objects__color__icontains=color, product__variant_objects__is_active=True) |
                Q(product__variants__icontains=f'"color": "{color}"')
            ).distinct()

        # Anotación de unidades vendidas por producto (solo ventas completadas)
        vendido_sq = (
            VentaPOSItem.objects
            .filter(product_id=OuterRef('product_id'), venta__status='completada')
            .values('product_id')
            .annotate(total=Sum('cantidad'))
            .values('total')[:1]
        )
        qs = qs.annotate(vendido=Coalesce(Subquery(vendido_sq, output_field=IntegerField()), 0))

        return qs


class PublicCategoryListView(generics.ListAPIView):
    """
    Endpoint público: /api/public/{vendor_slug}/categories/
    Retorna solo categorías raíz (parent=None) con sus subcategorías anidadas.
    """
    serializer_class = CategoryWithSubcategoriesSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        vendor_slug = self.kwargs['vendor_slug']
        return (
            Category.objects.filter(
                vendor__slug=vendor_slug,
                parent=None,
                is_active=True,
            )
            .prefetch_related('subcategories')
            .order_by('order', 'name')
        )
