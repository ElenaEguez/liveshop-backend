from django.contrib import admin
from .models import (
    Vendor,
    TeamMember,
    CustomRole,
    Sucursal,
    Almacen,
    KardexMovimiento,
    Caja,
    TurnoCaja,
    TicketConfig,
    Promocion,
    ConteoFisico,
    ConteoFisicoItem,
    TransferenciaAlmacen,
    TransferenciaAlmacenItem,
)


def _get_almacen_principal(vendor):
    """
    # MODO SIMPLE - retorna primer almacén activo
    # de la sucursal principal del vendor.
    """
    sucursal = (
        Sucursal.objects.filter(vendor=vendor, activa=True)
        .order_by('-es_principal', 'id')
        .first()
    )
    if not sucursal:
        return None
    return (
        Almacen.objects.filter(sucursal=sucursal, activo=True)
        .order_by('id')
        .first()
    )


@admin.action(description='✓ Activar modo simple + crear inventario inicial')
def activar_modo_simple(modeladmin, request, queryset):
    """
    # MODO SIMPLE - acción admin para activar cuenta simple.
    # Marca modo_simple=True y crea Inventory(quantity=0)
    # para productos existentes del vendor.
    # Solo opera sobre vendors con modo_simple=False
    # para evitar doble ejecución.
    """
    from products.models import Inventory, ProductVariant

    vendors_activados = 0
    productos_procesados = 0
    errores = []

    for vendor in queryset:
        if vendor.modo_simple:
            errores.append(
                f'{vendor.nombre_tienda}: ya tiene modo_simple=True, omitido.'
            )
            continue

        tiene_kardex = vendor.products.filter(
            inventories__quantity__gt=0
        ).exists()
        if tiene_kardex:
            errores.append(
                f'{vendor.nombre_tienda}: tiene inventario real con stock > 0. '
                f'No se puede convertir a modo simple desde aquí. '
                f'Consultar con administrador técnico.'
            )
            continue

        almacen = _get_almacen_principal(vendor)

        vendor.modo_simple = True
        vendor.save(update_fields=['modo_simple'])
        vendors_activados += 1

        productos = vendor.products.filter(is_active=True)
        for product in productos:
            inv, created = Inventory.objects.get_or_create(
                product=product,
                almacen=almacen,
                defaults={
                    'quantity': 0,
                    'reserved_quantity': 0,
                    'is_active': True,
                }
            )
            if not created:
                if not inv.is_active:
                    inv.is_active = True
                    inv.save(update_fields=['is_active', 'updated_at'])

            variantes = ProductVariant.objects.filter(
                product=product, is_active=True
            )
            for v in variantes:
                if v.stock_extra != 0:
                    v.stock_extra = 0
                    v.save(update_fields=['stock_extra'])

            productos_procesados += 1

    partes = []
    if vendors_activados:
        partes.append(
            f'{vendors_activados} vendor(s) activado(s) como modo simple. '
            f'{productos_procesados} producto(s) con Inventory inicial creado.'
        )
    if errores:
        partes.append('Omitidos: ' + ' | '.join(errores))

    if vendors_activados:
        modeladmin.message_user(
            request,
            ' '.join(partes),
            level='success' if not errores else 'warning'
        )
    else:
        modeladmin.message_user(
            request,
            ' '.join(partes) or 'No se procesó ningún vendor.',
            level='warning'
        )


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


class ConteoFisicoItemInline(admin.TabularInline):
    model = ConteoFisicoItem
    extra = 0
    fields = ['producto', 'variante', 'stock_sistema',
              'stock_fisico', 'diferencia', 'contado_por']
    readonly_fields = ['diferencia']


@admin.register(ConteoFisico)
class ConteoFisicoAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendor', 'almacen', 'fecha',
                    'estado', 'created_at']
    list_filter = ['estado', 'vendor']
    inlines = [ConteoFisicoItemInline]
    readonly_fields = ['creado_por', 'aprobado_por',
                       'created_at', 'updated_at']


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
    list_display = ('nombre_tienda', 'user', 'moneda', 'is_verified', 'created_at', 'modo_simple')
    list_filter = ('is_verified', 'created_at', 'modo_simple')
    search_fields = ('nombre_tienda', 'user__email', 'user__nombre', 'user__apellido')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    actions = ['activar_modo_simple']
    fieldsets = (
        ('Información de la Tienda', {
            'fields': ('user', 'nombre_tienda', 'slug', 'descripcion', 'logo', 'moneda')
        }),
        ('Redes Sociales', {
            'fields': ('whatsapp', 'tiktok_url', 'facebook_url', 'instagram_url')
        }),
        ('Tienda Web', {
            'fields': ('transfer_discount_percent', 'precio_editable', 'modo_simple'),
            'description': 'Configura el descuento por pago con transferencia bancaria. Poner 0 para ocultar el segundo precio.',
        }),
        ('Estados', {
            'fields': ('is_verified', 'created_at', 'updated_at')
        }),
    )


class TransferenciaItemInline(admin.TabularInline):
    model = TransferenciaAlmacenItem
    extra = 0
    fields = ['producto', 'variante', 'cantidad']


@admin.register(TransferenciaAlmacen)
class TransferenciaAlmacenAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendor', 'almacen_origen',
                    'almacen_destino', 'estado',
                    'created_at']
    list_filter = ['estado', 'vendor']
    inlines = [TransferenciaItemInline]
    readonly_fields = ['creado_por', 'completado_por',
                       'created_at', 'updated_at']
