from datetime import date
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Payment, MetodoPago, Cupon, CategoriaGasto,
    VentaPOS, VentaPOSItem, VentaPOSPago, GastoOperativo, PagoCredito,
    Devolucion, DevolucionItem,
)
from vendors.models import TeamMember


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
    variant_detail = serializers.SerializerMethodField()

    class Meta:
        model = VentaPOSItem
        fields = ('id', 'product', 'product_name', 'variant', 'cantidad',
                  'precio_unitario', 'costo_unitario', 'subtotal', 'variant_detail')

    def get_variant_detail(self, obj):
        if not obj.variant:
            return ''
        parts = []
        if obj.variant.talla:
            parts.append(f"Talla: {obj.variant.talla}")
        if obj.variant.color:
            parts.append(f"Color: {obj.variant.color}")
        return ' / '.join(parts)


class VentaPOSPagoSerializer(serializers.ModelSerializer):
    metodo_pago_nombre = serializers.CharField(
        source='metodo_pago.nombre', read_only=True, allow_null=True)
    metodo_pago_tipo = serializers.CharField(
        source='metodo_pago.tipo', read_only=True, allow_null=True)

    class Meta:
        model = VentaPOSPago
        fields = ('id', 'metodo_pago', 'metodo_pago_nombre', 'metodo_pago_tipo',
                  'monto', 'orden')
        read_only_fields = (
            'id', 'metodo_pago', 'metodo_pago_nombre', 'metodo_pago_tipo',
            'monto', 'orden',
        )


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
    pagos = VentaPOSPagoSerializer(many=True, read_only=True)
    metodo_pago_nombre = serializers.CharField(
        source='metodo_pago.nombre', read_only=True, allow_null=True)
    sucursal_nombre = serializers.CharField(
        source='sucursal.nombre', read_only=True, allow_null=True)
    usuario_nombre = serializers.SerializerMethodField()
    usuario_rol_nombre = serializers.SerializerMethodField()
    caja_nombre = serializers.SerializerMethodField()
    monto_pagado = serializers.SerializerMethodField()
    saldo_pendiente = serializers.SerializerMethodField()
    monto_cobrado = serializers.SerializerMethodField()

    class Meta:
        model = VentaPOS
        fields = (
            'id', 'numero_ticket', 'vendor', 'sucursal', 'sucursal_nombre',
            'caja', 'caja_nombre', 'turno', 'cliente_nombre', 'cliente_telefono',
            'metodo_pago', 'metodo_pago_nombre', 'subtotal', 'descuento',
            'discount_percentage', 'discount_type',
            'total', 'monto_recibido', 'vuelto', 'cupon', 'status',
            'canal_venta', 'direccion_envio',
            'usuario', 'usuario_nombre', 'usuario_rol_nombre', 'es_credito', 'plazo_dias', 'fecha_vencimiento_credito',
            'notas', 'created_at', 'items', 'pagos',
            'monto_pagado', 'saldo_pendiente', 'monto_cobrado',
        )
        read_only_fields = ('id', 'numero_ticket', 'vendor', 'created_at')

    def get_usuario_nombre(self, obj):
        if not obj.usuario:
            return ''
        return obj.usuario.get_full_name() or obj.usuario.email

    def get_usuario_rol_nombre(self, obj):
        if not obj.usuario:
            return None
        tm = TeamMember.objects.filter(vendor=obj.vendor, user=obj.usuario, is_active=True).select_related('custom_role').first()
        if tm and tm.custom_role:
            return tm.custom_role.name
        if obj.vendor and obj.vendor.user_id == obj.usuario_id:
            return 'Propietario'
        return None

    def get_caja_nombre(self, obj):
        return obj.caja.nombre if obj.caja else None

    def get_monto_pagado(self, obj):
        from django.db.models import Sum
        total = obj.pagos_credito.aggregate(t=Sum('monto'))['t'] or 0
        return str(total)

    def get_saldo_pendiente(self, obj):
        from django.db.models import Sum
        pagado = obj.pagos_credito.aggregate(t=Sum('monto'))['t'] or 0
        saldo = max(obj.total - pagado, 0)
        return str(saldo)

    def get_monto_cobrado(self, obj):
        from django.db.models import Sum
        if obj.es_credito:
            pagado = obj.pagos_credito.aggregate(t=Sum('monto'))['t'] or 0
            return str(pagado)
        if obj.monto_recibido is not None:
            return str(obj.monto_recibido)
        return str(obj.total)


class VentaPOSItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    cantidad = serializers.IntegerField(min_value=1)
    precio_unitario = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True,
    )


class VentaPOSPagoInputSerializer(serializers.Serializer):
    metodo_pago_id = serializers.IntegerField()
    monto = serializers.DecimalField(max_digits=10, decimal_places=2)


class VentaPOSCreateSerializer(serializers.Serializer):
    sucursal_id = serializers.IntegerField()
    caja_id = serializers.IntegerField(required=False, allow_null=True)
    turno_id = serializers.IntegerField(required=False, allow_null=True)
    cliente_nombre = serializers.CharField(
        max_length=100, required=False, default='Genérico')
    cliente_telefono = serializers.CharField(
        required=False, allow_blank=True, default='')
    metodo_pago_id = serializers.IntegerField(required=False, allow_null=True)
    pagos = VentaPOSPagoInputSerializer(many=True, required=False)
    items = VentaPOSItemInputSerializer(many=True)
    descuento = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, default=0)
    discount_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None)
    discount_type = serializers.ChoiceField(
        choices=['PERCENT', 'FIXED'], required=False, allow_null=True, default=None)
    cupon_codigo = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None)
    monto_recibido = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True)
    es_credito = serializers.BooleanField(required=False, default=False)
    plazo_dias = serializers.IntegerField(required=False, allow_null=True)
    notas = serializers.CharField(required=False, allow_blank=True, default='')
    canal_venta = serializers.ChoiceField(
        choices=['TIENDA', 'LIVE', 'WEB', 'DOMICILIO', 'INTERPROVINCIAL', 'NACIONAL'],
        required=False, default='TIENDA')
    direccion_envio = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None)

    def _resolver_precio_item(self, item, vendor, sucursal_id):
        """Precio PEPS del lote o Product.price si el ítem no trae precio_unitario."""
        if item.get('precio_unitario') is not None or not vendor:
            return
        from products.models import Product
        from products.stock_service import get_precio_venta_lote
        from vendors.models import Almacen

        product = Product.objects.get(pk=item['product_id'], vendor=vendor)
        almacen_id = None
        if sucursal_id:
            alm = Almacen.objects.filter(
                sucursal_id=sucursal_id, activo=True,
            ).order_by('id').first()
            almacen_id = alm.id if alm else None
        precio_lote = get_precio_venta_lote(product.id, almacen_id)
        item['precio_unitario'] = precio_lote or product.price

    def _calcular_total(self, attrs, vendor):
        """Replica el cálculo de total de VentaPOSViewSet.create (sin tocar stock)."""
        items = attrs.get('items') or []
        sucursal_id = attrs.get('sucursal_id')
        for item in items:
            self._resolver_precio_item(item, vendor, sucursal_id)
        subtotal = sum(
            item['precio_unitario'] * item['cantidad'] for item in items
        )
        discount_pct = attrs.get('discount_percentage')
        if discount_pct and discount_pct > 0:
            descuento_manual = (subtotal * discount_pct / 100).quantize(Decimal('0.01'))
        else:
            descuento_manual = attrs.get('descuento') or Decimal('0')
        base = max(subtotal - descuento_manual, Decimal('0'))

        descuento_cupon = Decimal('0')
        cupon_codigo = attrs.get('cupon_codigo')
        if cupon_codigo and vendor:
            try:
                cupon = Cupon.objects.get(
                    codigo=cupon_codigo, vendor=vendor, activo=True)
            except Cupon.DoesNotExist:
                raise serializers.ValidationError(
                    {'cupon_codigo': 'Cupón inválido o inactivo.'})
            if cupon.usos_maximos and cupon.usos_actuales >= cupon.usos_maximos:
                raise serializers.ValidationError({'cupon_codigo': 'Cupón agotado.'})
            if cupon.fecha_vencimiento and cupon.fecha_vencimiento < date.today():
                raise serializers.ValidationError({'cupon_codigo': 'Cupón vencido.'})
            if not cupon.aplica_pos:
                raise serializers.ValidationError(
                    {'cupon_codigo': 'Este cupón no aplica para ventas POS.'})
            if cupon.tipo == 'porcentaje':
                descuento_cupon = (base * cupon.valor / 100).quantize(Decimal('0.01'))
            else:
                descuento_cupon = min(cupon.valor, base)

        return max(base - descuento_cupon, Decimal('0'))

    def validate(self, attrs):
        vendor = self.context.get('vendor')
        pagos = attrs.get('pagos') or []
        sucursal_id = attrs.get('sucursal_id')
        for item in attrs.get('items') or []:
            self._resolver_precio_item(item, vendor, sucursal_id)

        if pagos:
            if not vendor:
                raise serializers.ValidationError(
                    {'pagos': 'No se pudo validar el vendedor.'})

            total = self._calcular_total(attrs, vendor)
            suma_pagos = sum(p['monto'] for p in pagos)
            if suma_pagos != total:
                raise serializers.ValidationError({
                    'pagos': (
                        f'La suma de pagos ({suma_pagos}) debe igualar '
                        f'el total de la venta ({total}).'
                    ),
                })

            for p in pagos:
                mp_id = p['metodo_pago_id']
                if p['monto'] <= 0:
                    raise serializers.ValidationError({
                        'pagos': 'Cada monto de pago debe ser mayor a 0.',
                    })
                if not MetodoPago.objects.filter(
                    pk=mp_id, vendor=vendor, activo=True,
                ).exists():
                    raise serializers.ValidationError({
                        'pagos': f'Método de pago {mp_id} inválido o inactivo.',
                    })

        elif attrs.get('metodo_pago_id') and vendor:
            if not MetodoPago.objects.filter(
                pk=attrs['metodo_pago_id'], vendor=vendor, activo=True,
            ).exists():
                raise serializers.ValidationError({
                    'metodo_pago_id': 'Método de pago inválido o inactivo.',
                })

        return attrs


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


class DevolucionItemSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.SerializerMethodField()
    variante_detalle = serializers.SerializerMethodField()

    class Meta:
        model = DevolucionItem
        fields = ['id', 'venta_item', 'producto_nombre',
                  'variante_detalle', 'cantidad',
                  'precio_unitario', 'subtotal']
        read_only_fields = ['subtotal']

    def get_producto_nombre(self, obj):
        return obj.venta_item.product.name \
            if obj.venta_item.product else ''

    def get_variante_detalle(self, obj):
        v = obj.venta_item.variant
        if not v:
            return None
        return {
            'id': v.id,
            'talla': v.talla,
            'color': v.color,
            'color_hex': v.color_hex,
        }


class DevolucionSerializer(serializers.ModelSerializer):
    items = DevolucionItemSerializer(
        many=True, read_only=True)
    venta_ticket = serializers.SerializerMethodField()
    venta_total = serializers.SerializerMethodField()
    procesado_por_nombre = serializers.SerializerMethodField()
    metodo_pago_devolucion_nombre = serializers.CharField(
        source='metodo_pago_devolucion.nombre',
        read_only=True,
        default='',
    )

    class Meta:
        model = Devolucion
        fields = ['id', 'venta', 'venta_ticket',
                  'venta_total', 'tipo',
                  'tipo_resolucion', 'motivo',
                  'monto_devuelto', 'items',
                  'metodo_pago_devolucion',
                  'metodo_pago_devolucion_nombre',
                  'procesado_por',
                  'procesado_por_nombre', 'created_at']
        read_only_fields = ['tipo', 'monto_devuelto',
                            'procesado_por', 'created_at']
        extra_kwargs = {
            'metodo_pago_devolucion': {'required': True},
        }

    def validate_tipo_resolucion(self, value):
        if value != 'devolucion_dinero':
            raise serializers.ValidationError(
                'Solo se permite devolución de dinero.',
            )
        return value

    def validate_metodo_pago_devolucion(self, value):
        request = self.context.get('request')
        if not request or not value:
            return value
        vendor = getattr(request.user, 'vendor_profile', None)
        if vendor is None:
            from vendors.permissions import get_vendor_for_user
            vendor = get_vendor_for_user(request.user)
        if vendor and value.vendor_id != vendor.id:
            raise serializers.ValidationError(
                'El método de pago no pertenece a su tienda.',
            )
        if not value.activo:
            raise serializers.ValidationError(
                'El método de pago no está activo.',
            )
        return value

    def get_venta_ticket(self, obj):
        return obj.venta.numero_ticket \
            if obj.venta else ''

    def get_venta_total(self, obj):
        return float(obj.venta.total) \
            if obj.venta else 0

    def get_procesado_por_nombre(self, obj):
        return obj.procesado_por.get_full_name() \
            if obj.procesado_por else ''


class VentaPOSSimpleSerializer(serializers.ModelSerializer):
    """
    Serializer simplificado de VentaPOS para la búsqueda
    al crear una devolución.
    """
    items = serializers.SerializerMethodField()
    cliente = serializers.SerializerMethodField()

    class Meta:
        model = VentaPOS
        fields = ['id', 'numero_ticket', 'total',
                  'descuento', 'status', 'cliente',
                  'created_at', 'items']

    def get_cliente(self, obj):
        return {
            'nombre': obj.cliente_nombre or '',
            'telefono': obj.cliente_telefono or '',
        }

    def get_items(self, obj):
        return [
            {
                'id': item.id,
                'producto_id': item.product.id,
                'producto_nombre': item.product.name,
                'variante_id': item.variant.id
                if item.variant else None,
                'variante_detalle': {
                    'talla': item.variant.talla,
                    'color': item.variant.color,
                    'color_hex': item.variant.color_hex,
                } if item.variant else None,
                'cantidad': item.cantidad,
                'precio_unitario': float(item.precio_unitario),
                'subtotal': float(item.subtotal),
                'cantidad_devuelta': sum(
                    di.cantidad
                    for di in item.devoluciones.all()
                ),
            }
            for item in obj.items.select_related(
                'product', 'variant').prefetch_related(
                'devoluciones').all()
        ]
