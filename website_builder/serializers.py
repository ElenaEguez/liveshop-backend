from rest_framework import serializers
from django.utils.text import slugify

from products.models import Product, ProductImage, ProductVariant, Category
from vendors.models import Vendor
from payments.models import MetodoPago
from .models import CartOrder, CartOrderItem


class PublicVariantSerializer(serializers.ModelSerializer):
    size = serializers.CharField(source='talla')
    stock = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ['id', 'size', 'color', 'stock', 'price']

    def get_stock(self, obj):
        return obj.stock_extra if obj.stock_extra > 0 else obj.product.stock

    def get_price(self, obj):
        return str(obj.product.price)


class PublicProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['image']


class PublicCategoryInlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']


class PublicProductSerializer(serializers.ModelSerializer):
    images = PublicProductImageSerializer(many=True, read_only=True)
    variants = PublicVariantSerializer(
        source='variant_objects', many=True, read_only=True
    )
    category = PublicCategoryInlineSerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price',
            'images', 'variants', 'category', 'is_active',
        ]


class PublicCategorySerializer(serializers.ModelSerializer):
    slug = serializers.SerializerMethodField()
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'product_count']

    def get_slug(self, obj):
        return slugify(obj.name)

    def get_product_count(self, obj):
        vendor_slug = self.context.get('vendor_slug')
        qs = obj.products.filter(is_active=True)
        if vendor_slug:
            qs = qs.filter(vendor__slug=vendor_slug)
        return qs.count()


class PublicPaymentMethodSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='tipo')
    qr_image = serializers.SerializerMethodField()
    instructions = serializers.SerializerMethodField()

    class Meta:
        model = MetodoPago
        fields = ['type', 'qr_image', 'instructions']

    def get_qr_image(self, obj):
        if obj.tipo == 'qr' and obj.vendor.payment_qr_image:
            request = self.context.get('request')
            url = obj.vendor.payment_qr_image.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_instructions(self, obj):
        if obj.tipo == 'qr':
            return obj.vendor.payment_instructions
        return None


class PublicStoreSerializer(serializers.ModelSerializer):
    vendor_slug = serializers.CharField(source='slug')
    store_name = serializers.CharField(source='nombre_tienda')
    description = serializers.CharField(source='descripcion')
    instagram = serializers.CharField(source='instagram_url')
    facebook = serializers.CharField(source='facebook_url')
    banner_url = serializers.SerializerMethodField()
    payment_methods = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = [
            'vendor_slug', 'store_name', 'logo', 'banner_url',
            'description', 'whatsapp', 'instagram', 'facebook',
            'payment_methods',
        ]

    def get_banner_url(self, obj):
        try:
            banner = obj.website.banners.filter(is_active=True).order_by('order').first()
            if banner and banner.image:
                request = self.context.get('request')
                return request.build_absolute_uri(banner.image.url) if request else banner.image.url
        except Exception:
            pass
        return None

    def get_payment_methods(self, obj):
        methods = obj.metodos_pago.filter(activo=True)
        return PublicPaymentMethodSerializer(
            methods, many=True, context=self.context
        ).data


# ── Checkout serializers ───────────────────────────────────────────────────────

class CartOrderItemCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    quantity = serializers.IntegerField(min_value=1)


class CartOrderCreateSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=200)
    customer_phone = serializers.CharField(max_length=20)
    customer_email = serializers.CharField(max_length=254, required=False, allow_blank=True, default='')
    customer_address = serializers.CharField(required=False, allow_blank=True, default='')
    delivery_method = serializers.ChoiceField(choices=['pickup', 'delivery'])
    payment_method = serializers.ChoiceField(choices=['tigo_money', 'banco_union', 'efectivo'])
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    items = CartOrderItemCreateSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Se requiere al menos un ítem.")
        return value


class CartOrderItemDetailSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='product.id')
    product_name = serializers.CharField(source='product.name')

    class Meta:
        model = CartOrderItem
        fields = ['id', 'product_id', 'product_name', 'variant_id', 'quantity', 'unit_price', 'subtotal']


class CartOrderDetailSerializer(serializers.ModelSerializer):
    items = CartOrderItemDetailSerializer(many=True, read_only=True)
    qr_image = serializers.SerializerMethodField()
    payment_receipt_url = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)
    delivery_method_display = serializers.CharField(source='get_delivery_method_display', read_only=True)

    class Meta:
        model = CartOrder
        fields = [
            'id', 'customer_name', 'customer_phone', 'customer_email',
            'customer_address', 'delivery_method', 'delivery_method_display',
            'status', 'status_display', 'total_amount',
            'payment_method', 'payment_method_display',
            'payment_receipt', 'payment_receipt_url', 'notes', 'created_at',
            'items', 'qr_image',
        ]

    def get_qr_image(self, obj):
        if obj.payment_method != 'efectivo' and obj.vendor.payment_qr_image:
            request = self.context.get('request')
            url = obj.vendor.payment_qr_image.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_payment_receipt_url(self, obj):
        if obj.payment_receipt:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.payment_receipt.url) if request else obj.payment_receipt.url
        return None
