from decimal import Decimal

from rest_framework import serializers

from config.request_utils import secure_absolute_uri
from .models import (
    Vendor,
    TeamMember,
    CustomRole,
    Sucursal,
    Almacen,
    Caja,
    TurnoCaja,
    MovimientoCaja,
    TicketConfig,
    Comprobante,
    ConteoFisico,
    ConteoFisicoItem,
    TransferenciaAlmacen,
    TransferenciaAlmacenItem,
)
from users.serializers import UserProfileSerializer


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for vendor list and basic information"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = ('id', 'nombre_tienda', 'slug', 'logo', 'descripcion', 'user_email', 'user_name',
                  'whatsapp', 'tiktok_url', 'facebook_url', 'instagram_url', 'is_verified', 'created_at')
        read_only_fields = ('id', 'slug', 'created_at', 'is_verified')

    def get_user_name(self, obj):
        return obj.user.get_full_name()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if data.get('logo'):
            data['logo'] = secure_absolute_uri(request, data['logo'])
        return data


class VendorProfileSerializer(serializers.ModelSerializer):
    """Serializer for detailed vendor profile with user information"""
    user = UserProfileSerializer(read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model = Vendor
        fields = ('id', 'user', 'user_id', 'nombre_tienda', 'slug', 'logo', 'descripcion',
                  'whatsapp', 'tiktok_url', 'facebook_url', 'instagram_url',
                  'payment_qr_image', 'payment_instructions', 'accepted_payment_methods',
                  'inventory_method',
                  'is_verified', 'created_at', 'updated_at')
        read_only_fields = ('id', 'user', 'user_id', 'slug', 'created_at', 'updated_at', 'is_verified')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        for field in ('logo', 'payment_qr_image'):
            if data.get(field):
                data[field] = secure_absolute_uri(request, data[field])
        return data


class CustomRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomRole
        fields = (
            'id', 'name',
            'perm_products', 'perm_categories', 'perm_compras', 'perm_inventory',
            'perm_live_sessions', 'perm_my_store',
            'perm_orders', 'perm_payments', 'perm_team', 'perm_dashboard',
            'perm_pos', 'perm_warehouse', 'perm_expenses',
            'perm_arqueos', 'perm_ventas_pos', 'perm_devoluciones',
            'perm_conteos', 'perm_conteos_control', 'perm_transferencias',
            'perm_almacen', 'perm_proveedores', 'perm_configuracion',
            'perm_ecommerce_orders',
            'created_at',
        )
        read_only_fields = ('id', 'created_at')


class TeamMemberSerializer(serializers.ModelSerializer):
    user_email    = serializers.EmailField(source='user.email', read_only=True)
    user_name     = serializers.SerializerMethodField()
    custom_role_name = serializers.CharField(source='custom_role.name', read_only=True, allow_null=True)

    class Meta:
        model = TeamMember
        fields = ('id', 'user', 'user_email', 'user_name',
                  'custom_role', 'custom_role_name', 'is_active', 'invited_at')
        read_only_fields = ('id', 'invited_at', 'user_email', 'user_name', 'custom_role_name')

    def get_user_name(self, obj):
        return obj.user.get_full_name()


class AlmacenSerializer(serializers.ModelSerializer):
    stock_por_variante = serializers.SerializerMethodField()

    class Meta:
        model = Almacen
        fields = ('id', 'sucursal', 'nombre', 'activo', 'stock_por_variante')
        read_only_fields = ('id',)

    def get_stock_por_variante(self, obj):
        from products.models import Inventory, ProductVariant

        inventarios = Inventory.objects.filter(
            almacen=obj, is_active=True
        ).select_related('product').order_by('product__name')

        resultado = []
        for inv in inventarios:
            entry = {
                'inventory_id': inv.id,
                'producto_id': inv.product.id,
                'producto_nombre': inv.product.name,
                'quantity': inv.quantity,
                'reserved': inv.reserved_quantity,
                'disponible': inv.quantity - inv.reserved_quantity,
                'variante': None,
            }
            resultado.append(entry)

        for inv in Inventory.objects.filter(
            almacen=obj, is_active=True
        ).select_related('product'):
            variantes = ProductVariant.objects.filter(
                product=inv.product, is_active=True
            )
            for v in variantes:
                resultado.append({
                    'inventory_id': inv.id,
                    'producto_id': inv.product.id,
                    'producto_nombre': inv.product.name,
                    'quantity': v.stock_extra,
                    'reserved': 0,
                    'disponible': v.stock_extra,
                    'variante': {
                        'id': v.id,
                        'talla': v.talla,
                        'color': v.color,
                        'color_hex': v.color_hex,
                        'sku': v.sku,
                    },
                })

        return resultado


class SucursalSerializer(serializers.ModelSerializer):
    almacenes = AlmacenSerializer(many=True, read_only=True)

    class Meta:
        model = Sucursal
        fields = ('id', 'nombre', 'direccion', 'es_principal', 'activa', 'created_at', 'almacenes')
        read_only_fields = ('id', 'created_at')


class CajaSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = Caja
        fields = ('id', 'sucursal', 'sucursal_nombre', 'nombre', 'activa')
        read_only_fields = ('id', 'sucursal', 'sucursal_nombre')


class MovimientoCajaSerializer(serializers.ModelSerializer):
    usuario_email = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoCaja
        fields = ('id', 'turno', 'tipo', 'concepto', 'monto', 'usuario', 'usuario_email', 'created_at')
        read_only_fields = ('id', 'created_at', 'usuario', 'usuario_email')

    def get_usuario_email(self, obj):
        return obj.usuario.email if obj.usuario else None


class TurnoCajaSerializer(serializers.ModelSerializer):
    total_ventas = serializers.SerializerMethodField()
    total_ingresos_manuales = serializers.SerializerMethodField()
    total_retiros = serializers.SerializerMethodField()
    caja_nombre = serializers.SerializerMethodField()
    sucursal_nombre = serializers.SerializerMethodField()
    usuario_nombre = serializers.SerializerMethodField()
    usuario_email = serializers.SerializerMethodField()
    usuario_rol_nombre = serializers.SerializerMethodField()
    metodos_pago = serializers.SerializerMethodField()

    class Meta:
        model = TurnoCaja
        fields = (
            'id', 'caja', 'caja_nombre', 'sucursal_nombre',
            'usuario', 'usuario_email', 'usuario_nombre', 'usuario_rol_nombre',
            'status', 'monto_apertura', 'monto_cierre',
            'efectivo_esperado', 'diferencia_cierre',
            'fecha_apertura', 'fecha_cierre', 'notas_cierre',
            'total_ventas', 'total_ingresos_manuales', 'total_retiros',
            'metodos_pago',
        )
        read_only_fields = (
            'id', 'fecha_apertura', 'total_ventas',
            'total_ingresos_manuales', 'total_retiros',
            'caja_nombre', 'sucursal_nombre', 'usuario_email', 'usuario_nombre', 'usuario_rol_nombre',
            'metodos_pago',
        )

    def get_caja_nombre(self, obj):
        return obj.caja.nombre if obj.caja else None

    def get_sucursal_nombre(self, obj):
        return obj.caja.sucursal.nombre if obj.caja and obj.caja.sucursal else None

    def get_usuario_nombre(self, obj):
        if not obj.usuario:
            return None
        return obj.usuario.get_full_name() or obj.usuario.email

    def get_usuario_email(self, obj):
        return obj.usuario.email if obj.usuario else None

    def get_usuario_rol_nombre(self, obj):
        if not obj.usuario:
            return None
        vendor = obj.caja.sucursal.vendor if obj.caja and obj.caja.sucursal else None
        if not vendor:
            return None
        tm = TeamMember.objects.filter(vendor=vendor, user=obj.usuario, is_active=True).select_related('custom_role').first()
        if tm and tm.custom_role:
            return tm.custom_role.name
        if vendor.user_id == obj.usuario_id:
            return 'Propietario'
        return None

    def _safe_money_str(self, val):
        try:
            if val is None:
                return '0.00'
            d = val if isinstance(val, Decimal) else Decimal(str(val))
            return str(d.quantize(Decimal('0.01')))
        except Exception:
            return '0.00'

    def get_total_ventas(self, obj):
        try:
            return self._safe_money_str(obj.total_ventas)
        except Exception:
            return '0.00'

    def get_total_ingresos_manuales(self, obj):
        try:
            return self._safe_money_str(obj.total_ingresos_manuales)
        except Exception:
            return '0.00'

    def get_total_retiros(self, obj):
        try:
            return self._safe_money_str(obj.total_retiros)
        except Exception:
            return '0.00'

    def get_metodos_pago(self, obj):
        from django.db.models import Q
        try:
            resultado = {}
            ventas = obj.ventas.filter(
                Q(status='completada')
                | Q(es_credito=True, status__in=['credito', 'completada'])
            ).select_related('metodo_pago')
            for v in ventas:
                if v.es_credito:
                    nombre = 'Crédito'
                else:
                    nombre = v.metodo_pago.nombre if v.metodo_pago else 'Sin método'
                try:
                    monto_f = float(v.total) if v.total is not None else 0.0
                except (TypeError, ValueError):
                    monto_f = 0.0
                if nombre not in resultado:
                    resultado[nombre] = {'monto': 0.0, 'cantidad': 0}
                resultado[nombre]['monto'] += monto_f
                resultado[nombre]['cantidad'] += 1
            return resultado
        except Exception:
            return {}


class TicketConfigSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = TicketConfig
        fields = (
            'id', 'mostrar_logo', 'logo_url', 'nombre_empresa', 'ruc_nit',
            'direccion', 'telefono', 'texto_pie', 'mostrar_qr',
            'moneda', 'ancho_ticket',
        )
        read_only_fields = ('id', 'logo_url')

    def get_logo_url(self, obj):
        request = self.context.get('request')
        logo = obj.vendor.logo
        if not logo:
            return None
        if request:
            return secure_absolute_uri(request, logo.url)
        return secure_absolute_uri(None, logo.url)


class ComprobanteSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = Comprobante
        fields = ('id', 'tipo', 'tipo_display', 'serie', 'correlativo')
        read_only_fields = ('id', 'tipo_display')


class ConteoFisicoItemSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.SerializerMethodField()
    variante_detalle = serializers.SerializerMethodField()
    producto_requiere_variante = serializers.SerializerMethodField()

    class Meta:
        model = ConteoFisicoItem
        fields = ['id', 'producto', 'producto_nombre',
                  'variante', 'variante_detalle',
                  'producto_requiere_variante',
                  'stock_sistema', 'stock_fisico',
                  'diferencia', 'notas', 'contado_por']
        read_only_fields = ['diferencia', 'contado_por']

    def get_producto_nombre(self, obj):
        return obj.producto.name if obj.producto else ''

    def get_variante_detalle(self, obj):
        if not obj.variante:
            return None
        v = obj.variante
        return {
            'id': v.id, 'talla': v.talla,
            'color': v.color, 'color_hex': v.color_hex,
        }

    def get_producto_requiere_variante(self, obj):
        from products.stock_service import product_has_variants
        return product_has_variants(obj.producto_id)


class ConteoFisicoSerializer(serializers.ModelSerializer):
    items = ConteoFisicoItemSerializer(
        many=True, read_only=True)
    almacen_nombre = serializers.SerializerMethodField()
    creado_por_nombre = serializers.SerializerMethodField()
    aprobado_por_nombre = serializers.SerializerMethodField()
    total_diferencias = serializers.SerializerMethodField()
    items_con_diferencia = serializers.SerializerMethodField()

    class Meta:
        model = ConteoFisico
        fields = ['id', 'almacen', 'almacen_nombre', 'estado',
                  'fecha', 'notas', 'items',
                  'creado_por', 'creado_por_nombre',
                  'aprobado_por', 'aprobado_por_nombre',
                  'created_at', 'updated_at',
                  'total_diferencias', 'items_con_diferencia']
        read_only_fields = ['estado', 'creado_por',
                            'aprobado_por', 'created_at',
                            'updated_at']

    def get_almacen_nombre(self, obj):
        return obj.almacen.nombre if obj.almacen else ''

    def get_creado_por_nombre(self, obj):
        return obj.creado_por.get_full_name() if obj.creado_por else ''

    def get_aprobado_por_nombre(self, obj):
        if not obj.aprobado_por:
            return ''
        full = obj.aprobado_por.get_full_name()
        return full.strip() if full and full.strip() else (obj.aprobado_por.email or '')

    def get_total_diferencias(self, obj):
        return sum(
            abs(i.diferencia) for i in obj.items.all()
        )

    def get_items_con_diferencia(self, obj):
        return obj.items.exclude(diferencia=0).count()


class TransferenciaItemSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.SerializerMethodField()
    variante_detalle = serializers.SerializerMethodField()

    class Meta:
        model = TransferenciaAlmacenItem
        fields = ['id', 'producto', 'producto_nombre',
                  'variante', 'variante_detalle', 'cantidad']

    def get_producto_nombre(self, obj):
        return obj.producto.name if obj.producto else ''

    def get_variante_detalle(self, obj):
        if not obj.variante:
            return None
        v = obj.variante
        return {
            'id': v.id, 'talla': v.talla,
            'color': v.color, 'color_hex': v.color_hex,
        }


class TransferenciaAlmacenSerializer(serializers.ModelSerializer):
    items = TransferenciaItemSerializer(
        many=True, read_only=True)
    almacen_origen_nombre = serializers.SerializerMethodField()
    almacen_destino_nombre = serializers.SerializerMethodField()
    creado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = TransferenciaAlmacen
        fields = ['id', 'almacen_origen',
                  'almacen_origen_nombre',
                  'almacen_destino',
                  'almacen_destino_nombre',
                  'estado', 'notas', 'items',
                  'creado_por', 'creado_por_nombre',
                  'completado_por',
                  'created_at', 'updated_at']
        read_only_fields = ['estado', 'creado_por',
                            'completado_por',
                            'created_at', 'updated_at']

    def get_almacen_origen_nombre(self, obj):
        return obj.almacen_origen.nombre \
            if obj.almacen_origen else ''

    def get_almacen_destino_nombre(self, obj):
        return obj.almacen_destino.nombre \
            if obj.almacen_destino else ''

    def get_creado_por_nombre(self, obj):
        return obj.creado_por.get_full_name() \
            if obj.creado_por else ''
