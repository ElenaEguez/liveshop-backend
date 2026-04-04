from rest_framework import serializers
from .models import Category, Product, ProductImage, Inventory, ProductVariant
from vendors.models import KardexMovimiento


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


class ProductSerializer(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()
    vendor = serializers.PrimaryKeyRelatedField(read_only=True)
    purchase_cost = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price', 'stock', 'category',
            'is_active', 'variants', 'images', 'vendor', 'purchase_cost',
            'profit_margin_percent', 'barcode', 'internal_code', 'sell_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['vendor', 'images', 'variants', 'purchase_cost', 'created_at', 'updated_at']

    def get_purchase_cost(self, obj):
        inv = obj.inventories.filter(is_active=True).first()
        if inv and inv.purchase_cost is not None:
            return float(inv.purchase_cost)
        return None

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

    class Meta:
        model = Inventory
        fields = '__all__'


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ('id', 'talla', 'color', 'color_hex', 'sku', 'stock_extra', 'is_active')


class ProductPOSSerializer(serializers.ModelSerializer):
    """Serializer ligero para búsqueda POS."""
    stock_disponible = serializers.SerializerMethodField()
    variantes = ProductVariantSerializer(source='variant_objects', many=True, read_only=True)
    imagen_thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'barcode', 'internal_code', 'price',
            'purchase_cost', 'stock_disponible', 'variantes', 'imagen_thumbnail',
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


class KardexMovimientoSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='inventory.product.name', read_only=True)
    almacen_nombre = serializers.CharField(source='almacen.nombre', read_only=True, allow_null=True)
    usuario_email = serializers.EmailField(source='usuario.email', read_only=True, allow_null=True)
    usuario_nombre = serializers.SerializerMethodField()

    def get_usuario_nombre(self, obj):
        if not obj.usuario:
            return None
        full = obj.usuario.get_full_name()
        return full if full.strip() else obj.usuario.email

    class Meta:
        model = KardexMovimiento
        fields = (
            'id', 'inventory', 'product_name', 'almacen', 'almacen_nombre',
            'tipo', 'motivo', 'cantidad', 'stock_anterior', 'stock_actual',
            'costo_promedio', 'documento_ref', 'usuario', 'usuario_email', 'usuario_nombre',
            'notas', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'product_name', 'almacen_nombre', 'usuario_email', 'usuario_nombre')


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
