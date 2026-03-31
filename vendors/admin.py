from django.contrib import admin
from .models import Vendor, TeamMember, CustomRole, Sucursal, Almacen, KardexMovimiento, Caja, TurnoCaja, TicketConfig, Promocion


class CustomRoleInline(admin.TabularInline):
    model = CustomRole
    extra = 0
    fields = ['name', 'perm_products', 'perm_categories', 'perm_inventory',
              'perm_live_sessions', 'perm_my_store', 'perm_orders',
              'perm_payments', 'perm_team', 'perm_dashboard']


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    fields = ['user', 'custom_role', 'is_active', 'invited_at']
    readonly_fields = ['invited_at']
    extra = 0


class AlmacenInline(admin.TabularInline):
    model = Almacen
    extra = 0
    fields = ['nombre', 'activo']


class CajaInline(admin.TabularInline):
    model = Caja
    extra = 0
    fields = ['nombre', 'activa']


class SucursalInline(admin.TabularInline):
    model = Sucursal
    extra = 0
    fields = ['nombre', 'direccion', 'es_principal', 'activa']


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    inlines = [AlmacenInline, CajaInline]
    list_display = ['nombre', 'vendor', 'es_principal', 'activa', 'created_at']
    list_filter = ['activa', 'es_principal', 'vendor']
    search_fields = ['nombre', 'vendor__nombre_tienda']


@admin.register(Almacen)
class AlmacenAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'sucursal', 'activo']
    list_filter = ['activo', 'sucursal__vendor']
    search_fields = ['nombre', 'sucursal__nombre']


@admin.register(KardexMovimiento)
class KardexMovimientoAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'inventory', 'almacen', 'tipo', 'motivo', 'cantidad', 'stock_anterior', 'stock_actual', 'usuario']
    list_filter = ['tipo', 'motivo', 'created_at']
    search_fields = ['inventory__product__name', 'documento_ref', 'usuario__email']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'sucursal', 'activa']
    list_filter = ['activa', 'sucursal__vendor']
    search_fields = ['nombre', 'sucursal__nombre']


@admin.register(TurnoCaja)
class TurnoCajaAdmin(admin.ModelAdmin):
    list_display = ['caja', 'usuario', 'status', 'monto_apertura', 'monto_cierre', 'fecha_apertura', 'fecha_cierre']
    list_filter = ['status', 'caja__sucursal__vendor']
    search_fields = ['caja__nombre', 'usuario__email']
    readonly_fields = ['fecha_apertura', 'total_ventas']


@admin.register(TicketConfig)
class TicketConfigAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'nombre_empresa', 'moneda', 'ancho_ticket', 'mostrar_logo', 'mostrar_qr']
    search_fields = ['vendor__nombre_tienda', 'nombre_empresa']


@admin.register(Promocion)
class PromocionAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'vendor', 'fecha_inicio', 'fecha_fin', 'activa', 'orden']
    list_filter = ['activa', 'vendor']
    search_fields = ['titulo', 'vendor__nombre_tienda']


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    inlines = [CustomRoleInline, TeamMemberInline, SucursalInline]
    list_display = ('nombre_tienda', 'user', 'moneda', 'is_verified', 'created_at')
    list_filter = ('is_verified', 'created_at')
    search_fields = ('nombre_tienda', 'user__email', 'user__nombre', 'user__apellido')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    fieldsets = (
        ('Información de la Tienda', {
            'fields': ('user', 'nombre_tienda', 'slug', 'descripcion', 'logo', 'moneda')
        }),
        ('Redes Sociales', {
            'fields': ('whatsapp', 'tiktok_url', 'facebook_url', 'instagram_url')
        }),
        ('Estados', {
            'fields': ('is_verified', 'created_at', 'updated_at')
        }),
    )
