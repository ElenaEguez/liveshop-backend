from rest_framework import serializers
from .models import Reservation


class PublicReservationSerializer(serializers.ModelSerializer):
    cupon_codigo = serializers.CharField(required=False, allow_blank=True, write_only=True)
    variant_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Reservation
        fields = ['id', 'customer_name', 'customer_phone', 'product', 'quantity', 'notes',
                  'variant_detail', 'cupon_codigo', 'variant_id']

    def validate(self, data):
        from products.models import Inventory
        from django.db.models import Sum

        live_session = self.context.get('live_session')
        product = data.get('product')
        quantity = data.get('quantity', 1)

        if live_session.status != 'live':
            raise serializers.ValidationError(
                "Este live no está activo. No se pueden recibir pedidos."
            )

        inventory = Inventory.objects.filter(
            product=product, is_active=True
        ).first()

        base_stock = inventory.quantity if inventory else product.stock

        from .models import Reservation
        reserved = Reservation.objects.filter(
            product=product,
            status__in=['pending', 'confirmed', 'paid']
        ).aggregate(total=Sum('quantity'))['total'] or 0

        available = max(0, base_stock - reserved)

        if available < quantity:
            raise serializers.ValidationError(
                f"Stock insuficiente. Solo hay {available} unidades disponibles."
            )

        return data


class ReservationSerializer(serializers.ModelSerializer):
    total_price = serializers.ReadOnlyField()
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_price = serializers.DecimalField(
        source='product.price', max_digits=10, decimal_places=2, read_only=True
    )
    session_title = serializers.CharField(source='session.title', read_only=True)
    payment_receipt_image = serializers.SerializerMethodField()
    payment_method = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()

    def get_payment_receipt_image(self, obj):
        try:
            request = self.context.get('request')
            if obj.payment and obj.payment.receipt_image:
                return request.build_absolute_uri(obj.payment.receipt_image.url) if request else obj.payment.receipt_image.url
        except Exception:
            pass
        return None

    def get_payment_method(self, obj):
        try:
            return obj.payment.payment_method if obj.payment else None
        except Exception:
            return None

    def get_payment_reference(self, obj):
        try:
            return obj.payment.customer_reference if obj.payment else None
        except Exception:
            return None

    def get_payment_status(self, obj):
        try:
            return obj.payment.status if obj.payment else None
        except Exception:
            return None

    class Meta:
        model = Reservation
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
