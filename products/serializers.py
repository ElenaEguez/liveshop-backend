from rest_framework import serializers
from django.utils.text import slugify
from .models import Category, Product, ProductImage, Inventory, ProductVariant
from vendors.models import KardexMovimiento


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'
        # vendor is injected by CategoryViewSet.perform_create/perform_update — never from client
        read_only_fields = ['created_at', 'vendor', 'slug']

    def _build_unique_slug(self, *, name, vendor, instance=None):
        base_slug = slugify((name or '').strip()) or 'categoria'
        candidate = base_slug
        suffix = 2

        while True:
            qs = Category.objects.filter(vendor=vendor, slug=candidate)
            if instance is not None:
                qs = qs.exclude(pk=instance.pk)
            if not qs.exists():
                return candidate
            candidate = f'{base_slug}-{suffix}'
            suffix += 1

    def create(self, validated_data):
        # vendor is passed in by the ViewSet via serializer.save(vendor=...)
        if not (validated_data.get('slug') or '').strip():
            validated_data['slug'] = self._build_unique_slug(
                name=validated_data.get('name', ''),
                vendor=validated_data.get('vendor'),
            )
        return super().create(validated_data)

    def update(self, instance, validated_data):
        vendor = instance.vendor
        incoming_slug = (validated_data.get('slug') or '').strip()
        incoming_name = (validated_data.get('name') or instance.name or '').strip()

        if not incoming_slug:
            validated_data['slug'] = self._build_unique_slug(
                name=incoming_name,
                vendor=vendor,
                instance=instance,
            )

        return super().update(instance, validated_data)


class ProductSerializer(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()
    vendor = serializers.PrimaryKeyRelatedField(read_only=True)
    variantes = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price', 'stock', 'category',
            'is_active', 'variants', 'variantes', 'images', 'vendor',
            'barcode', 'internal_code', 'sell_by',
            'is_active_live', 'is_active_pos', 'is_active_web',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'vendor', 'images', 'variants', 'variantes',
            'price', 'stock', 'created_at', 'updated_at',
        ]

    def get_variantes(self, obj):
        qs = obj.variant_objects.filter(is_active=True).order_by('id')
        return ProductVariantSerializer(qs, many=True).data

    def get_images(self, obj):
        request = self.context.get('request')
        return [
            request.build_absolute_uri(img.image.url) if request else img.image.url
            for img in obj.images.all()
        ]

    def validate_barcode(self, value):
        """Convert empty string to None to avoid unique constraint violations."""
        if value == '':
            return None
        return value


class InventorySerializer(serializers.ModelSerializer):
    available_quantity = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_price = serializers.DecimalField(source='product.price', read_only=True, max_digits=10, decimal_places=2)
    vendido = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Inventory
        fields = '__all__'


class InventoryAggregatedSerializer(serializers.Serializer):
    """Misma forma que InventorySerializer para listados agrupados por producto."""
    id = serializers.IntegerField()
    product = serializers.IntegerField()
    product_name = serializers.CharField()
    product_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    quantity = serializers.IntegerField()
    reserved_quantity = serializers.IntegerField()
    available_quantity = serializers.IntegerField()
    purchase_cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True, required=False,
    )
    almacen = serializers.IntegerField(allow_null=True)
    is_active = serializers.BooleanField()
    low_stock_alert = serializers.IntegerField(required=False)
    created_at = serializers.DateTimeField(allow_null=True, required=False)
    updated_at = serializers.DateTimeField(allow_null=True, required=False)
    variante = serializers.JSONField(allow_null=True, required=False)
    vendido = serializers.IntegerField(required=False, default=0)
    variantes = serializers.ListField(child=serializers.DictField(), required=False)
    sin_asignar_variante = serializers.IntegerField(required=False, default=0)


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ('id', 'talla', 'color', 'color_hex', 'sku', 'stock_extra', 'is_active')


class ProductPOSSerializer(serializers.ModelSerializer):
    """Serializer ligero para búsqueda POS."""
    stock_disponible = serializers.SerializerMethodField()
    variantes = serializers.SerializerMethodField()
    imagen_thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'barcode', 'internal_code', 'price',
            'stock_disponible', 'sell_by', 'variantes', 'imagen_thumbnail',
        )

    def get_stock_disponible(self, obj):
        inv = obj.inventories.filter(is_active=True).first()
        if inv:
            return inv.quantity - inv.reserved_quantity
        return obj.stock

    def get_imagen_thumbnail(self, obj):
        request = self.context.get('request')
        img = obj.images.first()
        if img:
            return request.build_absolute_uri(img.image.url) if request else img.image.url
        return None

    def get_variantes(self, obj):
        variants = obj.variant_objects.filter(is_active=True).order_by('id')
        return ProductVariantSerializer(variants, many=True).data


class KardexMovimientoSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='inventory.product_id', read_only=True)
    product_name = serializers.CharField(source='inventory.product.name', read_only=True)
    almacen_nombre = serializers.CharField(source='almacen.nombre', read_only=True, allow_null=True)
    usuario_email = serializers.EmailField(source='usuario.email', read_only=True, allow_null=True)
    usuario_nombre = serializers.SerializerMethodField()
    variant_name = serializers.SerializerMethodField()

    def get_usuario_nombre(self, obj):
        if not obj.usuario:
            return None
        full = obj.usuario.get_full_name()
        return full if full.strip() else obj.usuario.email

    def get_variant_name(self, obj):
        if not obj.variant:
            return None
        parts = [p for p in [obj.variant.talla, obj.variant.color] if p]
        return ' / '.join(parts) if parts else None

    class Meta:
        model = KardexMovimiento
        fields = (
            'id', 'inventory', 'product_id', 'product_name', 'almacen', 'almacen_nombre',
            'tipo', 'motivo', 'cantidad', 'stock_anterior', 'stock_actual',
            'costo_promedio', 'documento_ref', 'usuario', 'usuario_email', 'usuario_nombre',
            'notas', 'created_at', 'variant_name',
        )
        read_only_fields = (
            'id', 'created_at', 'product_id', 'product_name', 'almacen_nombre',
            'usuario_email', 'usuario_nombre', 'variant_name',
        )


class POSScanProductSerializer(serializers.ModelSerializer):
    """Serializer para el endpoint de escaneo POS."""
    nombre = serializers.CharField(source='name')
    precio_venta = serializers.DecimalField(source='price', max_digits=10, decimal_places=2)
    unidad_venta = serializers.JSONField(source='sell_by')
    imagen = serializers.SerializerMethodField()
    categoria = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'nombre', 'barcode', 'internal_code', 'precio_venta',
            'stock', 'unidad_venta', 'imagen', 'categoria'
        ]

    def get_imagen(self, obj):
        img = obj.images.first()
        if img:
            request = self.context.get('request')
            return request.build_absolute_uri(img.image.url) if request else img.image.url
        return None

    def get_categoria(self, obj):
        return obj.category.name if obj.category else None


class SubcategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'order', 'is_active']


class CategoryWithSubcategoriesSerializer(serializers.ModelSerializer):
    subcategories = SubcategorySerializer(many=True, read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'order', 'is_active', 'subcategories']


class PublicProductSerializer(serializers.ModelSerializer):
    colors = serializers.SerializerMethodField()
    category = SubcategorySerializer(read_only=True)
    images = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'price', 'description',
            'category', 'colors', 'images', 'is_active',
        ]

    def get_colors(self, obj):
        """Devuelve lista de colores únicos de las variantes del producto."""
        return list(
            obj.variant_objects.filter(is_active=True)
            .exclude(color='')
            .values('color', 'color_hex')
            .distinct()
        )

    def get_images(self, obj):
        request = self.context.get('request')
        return [
            request.build_absolute_uri(img.image.url) if request else img.image.url
            for img in obj.images.all()
        ]
