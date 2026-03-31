from django.contrib import admin
from .models import (
    Payment, MetodoPago, Cupon, CategoriaGasto,
    VentaPOS, VentaPOSItem, GastoOperativo,
)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['reservation', 'amount', 'status', 'payment_method', 'submitted_at', 'confirmed_at']
    list_filter = ['status', 'payment_method']
    search_fields = ['reservation__customer_name', 'customer_reference']
    readonly_fields = ['created_at', 'submitted_at', 'confirmed_at']


@admin.register(MetodoPago)
class MetodoPagoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'vendor', 'tipo', 'activo', 'orden']
    list_filter = ['tipo', 'activo', 'vendor']
    search_fields = ['nombre', 'vendor__nombre_tienda']


@admin.register(Cupon)
class CuponAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'vendor', 'tipo', 'valor', 'usos_actuales', 'usos_maximos', 'fecha_vencimiento', 'activo']
    list_filter = ['tipo', 'activo', 'vendor', 'aplica_live', 'aplica_pos']
    search_fields = ['codigo', 'vendor__nombre_tienda']


@admin.register(CategoriaGasto)
class CategoriaGastoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'vendor']
    list_filter = ['vendor']
    search_fields = ['nombre']


class VentaPOSItemInline(admin.TabularInline):
    model = VentaPOSItem
    extra = 0
    fields = ['product', 'variant', 'cantidad', 'precio_unitario', 'costo_unitario', 'subtotal']
    readonly_fields = ['subtotal']


@admin.register(VentaPOS)
class VentaPOSAdmin(admin.ModelAdmin):
    inlines = [VentaPOSItemInline]
    list_display = ['numero_ticket', 'vendor', 'sucursal', 'caja', 'turno', 'cliente_nombre', 'total', 'metodo_pago', 'status', 'created_at']
    list_filter = ['status', 'vendor', 'sucursal', 'es_credito', 'created_at']
    search_fields = ['numero_ticket', 'cliente_nombre', 'cliente_telefono']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'


@admin.register(GastoOperativo)
class GastoOperativoAdmin(admin.ModelAdmin):
    list_display = ['concepto', 'vendor', 'sucursal', 'categoria', 'monto', 'fecha', 'status', 'usuario']
    list_filter = ['status', 'vendor', 'categoria', 'fecha']
    search_fields = ['concepto', 'vendor__nombre_tienda']
    date_hierarchy = 'fecha'
