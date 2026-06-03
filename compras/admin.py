from django.contrib import admin
from compras.models import Proveedor, OrdenCompra, OrdenCompraItem


class OrdenCompraItemInline(admin.TabularInline):
    model = OrdenCompraItem
    extra = 0
    fields = [
        'producto', 'variante', 'descripcion', 'cantidad',
        'costo_mercaderia', 'flete_unitario', 'porcentaje_ganancia',
        'precio_venta_sugerido', 'precio_unitario', 'subtotal',
    ]
    readonly_fields = ['subtotal']


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'vendor', 'telefono', 'email', 'activo']
    list_filter = ['activo', 'vendor']
    search_fields = ['nombre', 'email']


@admin.register(OrdenCompra)
class OrdenCompraAdmin(admin.ModelAdmin):
    list_display = ['numero', 'vendor', 'proveedor', 'fecha', 'estado', 'total']
    list_filter = ['estado', 'vendor']
    search_fields = ['numero']
    inlines = [OrdenCompraItemInline]
    readonly_fields = ['numero', 'subtotal', 'total', 'created_by', 'created_at', 'updated_at']
