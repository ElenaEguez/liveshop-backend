from django.contrib import admin
from .models import VendorWebsite, WebsitePage, WebsiteBanner, CartOrder, CartOrderItem


class WebsitePageInline(admin.TabularInline):
    model = WebsitePage
    extra = 0


class WebsiteBannerInline(admin.TabularInline):
    model = WebsiteBanner
    extra = 0


@admin.register(VendorWebsite)
class VendorWebsiteAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'is_published', 'theme', 'created_at']
    list_filter = ['is_published', 'theme']
    inlines = [WebsitePageInline, WebsiteBannerInline]


@admin.register(WebsitePage)
class WebsitePageAdmin(admin.ModelAdmin):
    list_display = ['title', 'website', 'slug', 'is_active', 'order']
    list_filter = ['is_active']
    prepopulated_fields = {'slug': ('title',)}


@admin.register(WebsiteBanner)
class WebsiteBannerAdmin(admin.ModelAdmin):
    list_display = ['title', 'website', 'is_active', 'order']
    list_filter = ['is_active']


class CartOrderItemInline(admin.TabularInline):
    model = CartOrderItem
    extra = 0
    readonly_fields = ['product', 'variant_id', 'quantity', 'unit_price', 'subtotal']


@admin.register(CartOrder)
class CartOrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'customer_name', 'customer_phone', 'vendor', 'delivery_method', 'status', 'total_amount', 'created_at']
    list_filter = ['status', 'delivery_method', 'payment_method', 'vendor']
    search_fields = ['customer_name', 'customer_phone', 'customer_email']
    readonly_fields = ['created_at', 'total_amount']
    inlines = [CartOrderItemInline]
