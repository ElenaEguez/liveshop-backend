from decimal import Decimal

from rest_framework import serializers
from .models import Vendor, TeamMember, CustomRole, Sucursal, Almacen, Caja, TurnoCaja, MovimientoCaja, TicketConfig, Comprobante
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


class CustomRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomRole
        fields = (
            'id', 'name',
            'perm_products', 'perm_categories', 'perm_inventory',
            'perm_live_sessions', 'perm_my_store',
            'perm_orders', 'perm_payments', 'perm_team', 'perm_dashboard',
            'perm_pos', 'perm_warehouse', 'perm_expenses',
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
    class Meta:
        model = Almacen
        fields = ('id', 'sucursal', 'nombre', 'activo')
        read_only_fields = ('id',)


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
    metodos_pago = serializers.SerializerMethodField()

    class Meta:
        model = TurnoCaja
        fields = (
            'id', 'caja', 'caja_nombre', 'sucursal_nombre',
            'usuario', 'usuario_email', 'usuario_nombre',
            'status', 'monto_apertura', 'monto_cierre',
            'efectivo_esperado', 'diferencia_cierre',
            'fecha_apertura', 'fecha_cierre', 'notas_cierre',
            'total_ventas', 'total_ingresos_manuales', 'total_retiros',
            'metodos_pago',
        )
        read_only_fields = (
            'id', 'fecha_apertura', 'total_ventas',
            'total_ingresos_manuales', 'total_retiros',
            'caja_nombre', 'sucursal_nombre', 'usuario_email', 'usuario_nombre',
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
        from django.db.models import Count, Sum
        try:
            ventas = (
                obj.ventas
                .filter(status='completada')
                .values('metodo_pago__nombre')
                .annotate(monto_total=Sum('total'), cantidad=Count('id'))
            )
            resultado = {}
            for v in ventas:
                nombre = v['metodo_pago__nombre'] or 'Sin método'
                mt = v['monto_total']
                try:
                    monto_f = float(mt) if mt is not None else 0.0
                except (TypeError, ValueError):
                    monto_f = 0.0
                resultado[nombre] = {
                    'monto': monto_f,
                    'cantidad': v['cantidad'],
                }
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
            return request.build_absolute_uri(logo.url)
        return logo.url


class ComprobanteSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = Comprobante
        fields = ('id', 'tipo', 'tipo_display', 'serie', 'correlativo')
        read_only_fields = ('id', 'tipo_display')
