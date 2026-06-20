from decimal import Decimal, ROUND_HALF_UP

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
    disponible = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ['id', 'size', 'color', 'color_hex', 'stock', 'price', 'disponible']

    def get_stock(self, obj):
        return obj.stock_extra if obj.stock_extra > 0 else obj.product.stock

    def get_price(self, obj):
        return str(obj.product.price)

    def get_disponible(self, obj):
        stock = obj.stock_extra if obj.stock_extra > 0 else obj.product.stock
        return stock > 0


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
    stock_available = serializers.SerializerMethodField()
    discount_percent = serializers.SerializerMethodField()
    transfer_price = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price', 'compare_at_price',
            'web_is_bestseller', 'web_is_new',
            'stock_available', 'discount_percent', 'transfer_price',
            'images', 'variants', 'category', 'is_active',
        ]

    def get_stock_available(self, obj):
        from products.inventory_stock import variant_stock_breakdown
        return variant_stock_breakdown(obj.id)['disponible_total'] > 0

    def get_discount_percent(self, obj):
        compare = obj.compare_at_price
        price = obj.price
        if not compare or not price or compare <= price:
            return None
        pct = ((compare - price) / compare) * 100
        return int(pct.quantize(Decimal('1')))

    def get_transfer_price(self, obj):
        """
        Precio con descuento por transferencia.
        El porcentaje se configura en Vendor.transfer_discount_percent.
        Si es 0, no se muestra segundo precio (retorna None).
        """
        if not obj.price:
            return None
        discount_pct = getattr(obj.vendor, 'transfer_discount_percent', Decimal('10.00'))
        if not discount_pct or discount_pct <= 0:
            return None
        factor = (Decimal('100') - discount_pct) / Decimal('100')
        discounted = (obj.price * factor).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        return str(discounted)


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
    delivery_method = serializers.ChoiceField(choices=['pickup', 'delivery', 'envio_nacional', 'envio_scz'])
    payment_method = serializers.ChoiceField(choices=['tigo_money', 'banco_union', 'efectivo'])
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    cupon_codigo = serializers.CharField(required=False, allow_blank=True, default='')
    items = CartOrderItemCreateSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Se requiere al menos un ítem.")
        return value


class CartOrderItemDetailSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='product.id')
    product_name = serializers.CharField(source='product.name')
    variant_detail = serializers.SerializerMethodField()

    class Meta:
        model = CartOrderItem
        fields = ['id', 'product_id', 'product_name', 'variant_id', 'variant_detail', 'quantity', 'unit_price', 'subtotal']

    def get_variant_detail(self, obj):
        if not obj.variant_id:
            return ''
        try:
            variant = ProductVariant.objects.get(pk=obj.variant_id)
        except ProductVariant.DoesNotExist:
            return ''
        parts = []
        if variant.talla:
            parts.append(f"Talla: {variant.talla}")
        if variant.color:
            parts.append(f"Color: {variant.color}")
        return ' / '.join(parts)


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
            return obj.payment_receipt.url  # URL relativa /media/... evita mixed-content HTTPS
        return None
