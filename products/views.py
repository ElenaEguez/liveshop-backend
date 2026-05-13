import json
from urllib.parse import urlparse
from rest_framework import generics, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.db.models import IntegerField, OuterRef, Q, Subquery, Sum, Min, Max
from django.db.models.functions import Coalesce
from .models import Category, Product, ProductImage, Inventory, ProductVariant
from vendors.permissions import get_vendor_for_user
from vendors.models import Almacen
from .serializers import (
    CategorySerializer, CategoryWithSubcategoriesSerializer,
    ProductSerializer, InventorySerializer, InventoryAggregatedSerializer, ProductVariantSerializer,
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
    MAX_IMAGES = 3
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

    def _parse_inventory_distribution(self, request):
        raw = request.data.get('inventory_distribution', '[]')
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = []
        else:
            parsed = raw if isinstance(raw, list) else []

        out = []
        seen_almacenes = set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            almacen_id = row.get('almacen_id')
            qty_raw = row.get('quantity', 0)
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                qty = 0
            if almacen_id in (None, '', 'null'):
                normalized_almacen = None
            else:
                try:
                    normalized_almacen = int(almacen_id)
                except (TypeError, ValueError):
                    raise ValidationError({'inventory_distribution': f'Almacén inválido: {almacen_id}.'})
            if normalized_almacen is not None:
                if normalized_almacen in seen_almacenes:
                    raise ValidationError({'inventory_distribution': 'No puedes repetir el mismo almacén en más de una fila.'})
                seen_almacenes.add(normalized_almacen)
            out.append({'almacen_id': normalized_almacen, 'quantity': max(qty, 0)})
        return out

    def _validate_stock_consistency(self, request, *, variants, distribution):
        stock_raw = request.data.get('stock', 0)
        try:
            stock_total = int(stock_raw)
        except (TypeError, ValueError):
            stock_total = 0

        if variants:
            variant_sum = 0
            for v in variants:
                if not isinstance(v, dict):
                    continue
                try:
                    variant_sum += int(v.get('stock', 0))
                except (TypeError, ValueError):
                    pass
            if variant_sum != stock_total:
                raise ValidationError({'variants': f'La suma de variantes ({variant_sum}) debe ser igual al stock total ({stock_total}).'})

        if distribution:
            dist_sum = sum(int(r.get('quantity', 0)) for r in distribution)
            if dist_sum != stock_total:
                raise ValidationError({'inventory_distribution': f'La suma distribuida ({dist_sum}) debe ser igual al stock total ({stock_total}).'})

    def _sync_inventory_distribution(self, product, vendor, distribution):
        if not distribution:
            inv, _ = Inventory.objects.get_or_create(
                product=product,
                almacen=None,
                defaults={'quantity': product.stock}
            )
            inv.quantity = product.stock
            inv.is_active = True
            inv.save(update_fields=['quantity', 'is_active'])
            return

        desired = {}
        for row in distribution:
            almacen_id = row.get('almacen_id')
            qty = int(row.get('quantity', 0))
            if almacen_id is not None:
                almacen = Almacen.objects.filter(id=almacen_id, sucursal__vendor=vendor, activo=True).first()
                if not almacen:
                    raise ValidationError({'inventory_distribution': f'Almacén inválido: {almacen_id}.'})
            desired[almacen_id] = desired.get(almacen_id, 0) + qty

        existing = list(product.inventories.all())
        existing_map = {(inv.almacen_id): inv for inv in existing}

        for almacen_id, qty in desired.items():
            inv = existing_map.get(almacen_id)
            if inv is None:
                inv = Inventory(product=product, almacen_id=almacen_id, reserved_quantity=0)
            if inv.reserved_quantity and qty < inv.reserved_quantity:
                raise ValidationError({'inventory_distribution': f'No puedes asignar menos de {inv.reserved_quantity} unidades en almacén {almacen_id or "sin almacén"}.'})
            inv.quantity = qty
            inv.is_active = True
            inv.save()

        desired_ids = set(desired.keys())
        for inv in existing:
            if inv.almacen_id in desired_ids:
                continue
            if inv.reserved_quantity and inv.reserved_quantity > 0:
                raise ValidationError({'inventory_distribution': f'No se puede quitar almacén {inv.almacen_id or "sin almacén"} porque tiene reserva activa.'})
            inv.quantity = 0
            inv.is_active = False
            inv.save(update_fields=['quantity', 'is_active'])

    def _normalize_keep_images(self, raw_keep):
        if not raw_keep:
            return set()
        if isinstance(raw_keep, str):
            try:
                raw_keep = json.loads(raw_keep)
            except (json.JSONDecodeError, TypeError):
                raw_keep = []
        if not isinstance(raw_keep, list):
            return set()
        normalized = set()
        for item in raw_keep:
            if not item:
                continue
            val = str(item).strip()
            if not val:
                continue
            parsed = urlparse(val)
            normalized.add(parsed.path or val)
        return normalized

    def _sync_existing_images_on_update(self, product, request):
        if 'keep_images' not in request.data:
            return
        keep_paths = self._normalize_keep_images(request.data.get('keep_images'))
        for img in product.images.all():
            current = img.image.url if img.image else ''
            if current not in keep_paths and urlparse(current).path not in keep_paths:
                img.delete()

    def _validate_images_limit(self, product, request, *, is_update=False):
        incoming_count = len(request.FILES.getlist('images'))
        if not is_update:
            if incoming_count > self.MAX_IMAGES:
                raise ValidationError({'images': f'Máximo {self.MAX_IMAGES} imágenes por producto.'})
            return
        has_keep_images = 'keep_images' in request.data
        keep_paths = self._normalize_keep_images(request.data.get('keep_images'))
        if has_keep_images:
            existing_kept = 0
            for img in product.images.all():
                current = img.image.url if img.image else ''
                if current in keep_paths or urlparse(current).path in keep_paths:
                    existing_kept += 1
        else:
            existing_kept = product.images.count()
        if existing_kept + incoming_count > self.MAX_IMAGES:
            raise ValidationError({'images': f'Máximo {self.MAX_IMAGES} imágenes por producto.'})

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

    def perform_create(self, serializer):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise ValidationError({'detail': 'Sin perfil de vendedor asociado.'})
        variants = self._parse_variants(self.request)
        distribution = self._parse_inventory_distribution(self.request)
        self._validate_stock_consistency(self.request, variants=variants, distribution=distribution)
        self._validate_images_limit(None, self.request, is_update=False)
        product = serializer.save(
            vendor=vendor,
            price=0,
            variants=variants,
        )
        self._save_images(product, self.request)
        self._sync_variant_objects(product, variants)
        self._sync_inventory_distribution(product, vendor, distribution)

    def perform_update(self, serializer):
        current = self.get_object()
        self._validate_images_limit(current, self.request, is_update=True)
        variants = self._parse_variants(self.request)
        has_distribution_payload = 'inventory_distribution' in self.request.data
        distribution = self._parse_inventory_distribution(self.request) if has_distribution_payload else []
        if has_distribution_payload:
            self._validate_stock_consistency(self.request, variants=variants, distribution=distribution)
        product = serializer.save(variants=variants)
        self._sync_existing_images_on_update(product, self.request)
        self._save_images(product, self.request)
        self._sync_variant_objects(product, variants)
        vendor = get_vendor_for_user(self.request.user)
        if has_distribution_payload:
            self._sync_inventory_distribution(product, vendor, distribution)

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


class InventoryListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class InventoryViewSet(viewsets.ModelViewSet):
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = InventoryListPagination

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

        vendido_sq = (
            VentaPOSItem.objects
            .filter(product_id=OuterRef('product_id'), venta__status='completada')
            .values('product_id')
            .annotate(total=Sum('cantidad'))
            .values('total')[:1]
        )
        qs = qs.annotate(vendido=Coalesce(Subquery(vendido_sq, output_field=IntegerField()), 0))

        return qs

    def list(self, request, *args, **kwargs):
        if request.query_params.get('almacen_id'):
            return super().list(request, *args, **kwargs)

        qs = self.filter_queryset(self.get_queryset())
        agg_qs = (
            qs.values('product_id')
            .annotate(
                quantity=Sum('quantity'),
                reserved_quantity=Sum('reserved_quantity'),
                id=Min('id'),
                purchase_cost=Min('purchase_cost'),
                almacen=Min('almacen_id'),
                vendido=Max('vendido'),
                product_name=Min('product__name'),
                product_price=Min('product__price'),
                low_stock_alert=Min('low_stock_alert'),
            )
            .order_by('product_name')
        )

        page = self.paginate_queryset(agg_qs)
        iterable = page if page is not None else agg_qs

        rows = []
        for row in iterable:
            q = int(row['quantity'] or 0)
            r = int(row['reserved_quantity'] or 0)
            low_alert = int(row['low_stock_alert'] or 5)
            rows.append({
                'id': row['id'],
                'product': row['product_id'],
                'product_name': row['product_name'],
                'product_price': row['product_price'],
                'quantity': q,
                'reserved_quantity': r,
                'available_quantity': q - r,
                'purchase_cost': row['purchase_cost'],
                'almacen': row['almacen'],
                'is_active': True,
                'low_stock_alert': low_alert,
                'created_at': None,
                'updated_at': None,
                'variante': None,
                'vendido': row['vendido'],
            })

        ser = InventoryAggregatedSerializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(ser.data)
        return Response(ser.data)


class InventoryAdjustView(APIView):
    """
    Ajusta el stock de un registro Inventory.
    PATCH body: { "cantidad": <int>, "nota": "<str>" }
    cantidad positivo = entrada, negativo = salida.
    Crea KardexMovimiento automáticamente.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        from vendors.models import KardexMovimiento

        try:
            inv = Inventory.objects.select_related(
                'product', 'almacen'
            ).get(pk=pk)
        except Inventory.DoesNotExist:
            return Response({'error': 'Inventario no encontrado'}, status=404)

        cantidad = request.data.get('cantidad', 0)
        nota = request.data.get('nota', 'Ajuste manual')

        try:
            cantidad = int(cantidad)
        except (ValueError, TypeError):
            return Response({'error': 'cantidad debe ser un número entero'}, status=400)

        if cantidad == 0:
            return Response({'error': 'cantidad no puede ser 0'}, status=400)

        nuevo_stock = inv.quantity + cantidad
        if nuevo_stock < 0:
            return Response(
                {'error': f'Stock insuficiente. Stock actual: {inv.quantity}'},
                status=400
            )

        from django.db import transaction
        with transaction.atomic():
            stock_anterior = inv.quantity
            inv.quantity = nuevo_stock
            inv.save(update_fields=['quantity'])

            tipo = 'entrada' if cantidad > 0 else 'salida'
            KardexMovimiento.objects.create(
                inventory=inv,
                almacen=inv.almacen,
                tipo=tipo,
                motivo='ajuste_manual',
                cantidad=abs(cantidad),
                stock_anterior=stock_anterior,
                stock_actual=nuevo_stock,
                documento_ref=f'AJUSTE-{inv.id}',
                usuario=request.user,
                notas=nota,
            )

        return Response({
            'ok': True,
            'inventory_id': inv.id,
            'producto': inv.product.name,
            'stock_anterior': stock_anterior,
            'stock_nuevo': inv.quantity,
            'cantidad': cantidad,
        })


class VariantStockAdjustView(APIView):
    """
    Ajusta stock_extra de una ProductVariant.
    PATCH body: { "cantidad": <int>, "nota": "<str>" }
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        try:
            variante = ProductVariant.objects.select_related('product').get(pk=pk)
        except ProductVariant.DoesNotExist:
            return Response({'error': 'Variante no encontrada'}, status=404)

        cantidad = request.data.get('cantidad', 0)
        try:
            cantidad = int(cantidad)
        except (ValueError, TypeError):
            return Response({'error': 'cantidad debe ser un número entero'}, status=400)

        if cantidad == 0:
            return Response({'error': 'cantidad no puede ser 0'}, status=400)

        stock_anterior = variante.stock_extra
        nuevo_stock = stock_anterior + cantidad
        if nuevo_stock < 0:
            return Response(
                {'error': f'Stock insuficiente. Stock actual: {variante.stock_extra}'},
                status=400
            )

        variante.stock_extra = nuevo_stock
        variante.save(update_fields=['stock_extra'])

        return Response({
            'ok': True,
            'variante_id': variante.id,
            'talla': variante.talla,
            'color': variante.color,
            'stock_anterior': stock_anterior,
            'stock_nuevo': variante.stock_extra,
        })


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
