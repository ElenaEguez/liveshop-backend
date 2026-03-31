from django.contrib import admin
from .models import Category, Product, ProductImage, Inventory, ProductVariant

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ['talla', 'color', 'color_hex', 'sku', 'stock_extra', 'is_active']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductVariantInline]
    list_display = ['name', 'vendor', 'category', 'price', 'purchase_cost', 'barcode', 'stock', 'is_active', 'created_at']
    list_filter = ['is_active', 'category', 'vendor']
    search_fields = ['name', 'sku', 'barcode', 'internal_code']


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ['product', 'talla', 'color', 'color_hex', 'sku', 'stock_extra', 'is_active']
    list_filter = ['is_active', 'product__vendor']
    search_fields = ['product__name', 'sku', 'talla', 'color']

@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ['product', 'image']

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ['product', 'almacen', 'quantity', 'reserved_quantity', 'available_quantity', 'is_low_stock', 'is_active', 'updated_at']
    list_filter = ['is_active', 'almacen__sucursal__vendor']
    search_fields = ['product__name']
    readonly_fields = ['available_quantity', 'is_low_stock']
