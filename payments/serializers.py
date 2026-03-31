from rest_framework import serializers
from .models import (
    Payment, MetodoPago, Cupon, CategoriaGasto,
    VentaPOS, VentaPOSItem, GastoOperativo, PagoCredito,
)


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='reservation.customer_name', read_only=True)
    customer_phone = serializers.CharField(source='reservation.customer_phone', read_only=True)
    product_name = serializers.CharField(source='reservation.product.name', read_only=True)
    session_title = serializers.CharField(source='reservation.session.title', read_only=True)

    class Meta:
        model = Payment
        fields = '__all__'
        read_only_fields = ['status', 'confirmed_at', 'created_at']


class PaymentConfirmSerializer(serializers.Serializer):
    vendor_notes = serializers.CharField(required=False, allow_blank=True)
    action = serializers.ChoiceField(choices=['confirm', 'reject'])


# ─── POS Serializers ─────────────────────────────────────────────────────────

class MetodoPagoSerializer(serializers.ModelSerializer):
    class Meta:
        model = MetodoPago
        fields = ('id', 'nombre', 'tipo', 'icono', 'activo', 'orden')
        read_only_fields = ('id',)


class CuponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cupon
        fields = (
            'id', 'codigo', 'tipo', 'valor', 'usos_maximos', 'usos_actuales',
            'fecha_vencimiento', 'activo', 'aplica_live', 'aplica_pos',
        )
        read_only_fields = ('id', 'usos_actuales')


class CategoriaGastoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaGasto
        fields = ('id', 'nombre')
        read_only_fields = ('id',)


class VentaPOSItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True, allow_null=True)

    class Meta:
        model = VentaPOSItem
        fields = ('id', 'product', 'product_name', 'variant', 'cantidad',
                  'precio_unitario', 'costo_unitario', 'subtotal')


class PagoCreditoSerializer(serializers.ModelSerializer):
    metodo_pago_nombre = serializers.CharField(
        source='metodo_pago.nombre', read_only=True, allow_null=True)
    usuario_nombre = serializers.SerializerMethodField()

    class Meta:
        model = PagoCredito
        fields = ('id', 'monto', 'metodo_pago', 'metodo_pago_nombre',
                  'notas', 'usuario_nombre', 'created_at')
        read_only_fields = ('id', 'usuario_nombre', 'created_at')

    def get_usuario_nombre(self, obj):
        if not obj.usuario:
            return ''
        return obj.usuario.get_full_name() or obj.usuario.email


class VentaPOSSerializer(serializers.ModelSerializer):
    items = VentaPOSItemSerializer(many=True, read_only=True)
    metodo_pago_nombre = serializers.CharField(
        source='metodo_pago.nombre', read_only=True, allow_null=True)
    sucursal_nombre = serializers.CharField(
        source='sucursal.nombre', read_only=True, allow_null=True)
    monto_pagado = serializers.SerializerMethodField()
    saldo_pendiente = serializers.SerializerMethodField()

    class Meta:
        model = VentaPOS
        fields = (
            'id', 'numero_ticket', 'vendor', 'sucursal', 'sucursal_nombre',
            'caja', 'turno', 'cliente_nombre', 'cliente_telefono',
            'metodo_pago', 'metodo_pago_nombre', 'subtotal', 'descuento',
            'total', 'monto_recibido', 'vuelto', 'cupon', 'status',
            'usuario', 'es_credito', 'plazo_dias', 'fecha_vencimiento_credito',
            'notas', 'created_at', 'items', 'monto_pagado', 'saldo_pendiente',
        )
        read_only_fields = ('id', 'numero_ticket', 'vendor', 'created_at')

    def get_monto_pagado(self, obj):
        from django.db.models import Sum
        total = obj.pagos_credito.aggregate(t=Sum('monto'))['t'] or 0
        return str(total)

    def get_saldo_pendiente(self, obj):
        from django.db.models import Sum
        pagado = obj.pagos_credito.aggregate(t=Sum('monto'))['t'] or 0
        saldo = max(obj.total - pagado, 0)
        return str(saldo)


class VentaPOSItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    cantidad = serializers.IntegerField(min_value=1)
    precio_unitario = serializers.DecimalField(max_digits=10, decimal_places=2)


class VentaPOSCreateSerializer(serializers.Serializer):
    sucursal_id = serializers.IntegerField()
    caja_id = serializers.IntegerField(required=False, allow_null=True)
    turno_id = serializers.IntegerField(required=False, allow_null=True)
    cliente_nombre = serializers.CharField(
        max_length=100, required=False, default='Genérico')
    cliente_telefono = serializers.CharField(
        required=False, allow_blank=True, default='')
    metodo_pago_id = serializers.IntegerField(required=False, allow_null=True)
    items = VentaPOSItemInputSerializer(many=True)
    descuento = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, default=0)
    cupon_codigo = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None)
    monto_recibido = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True)
    es_credito = serializers.BooleanField(required=False, default=False)
    plazo_dias = serializers.IntegerField(required=False, allow_null=True)
    notas = serializers.CharField(required=False, allow_blank=True, default='')


class GastoOperativoSerializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(
        source='categoria.nombre', read_only=True, allow_null=True)
    sucursal_nombre = serializers.CharField(
        source='sucursal.nombre', read_only=True, allow_null=True)

    class Meta:
        model = GastoOperativo
        fields = (
            'id', 'sucursal', 'sucursal_nombre', 'categoria', 'categoria_nombre',
            'concepto', 'monto', 'fecha', 'status', 'usuario', 'notas', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'usuario')
