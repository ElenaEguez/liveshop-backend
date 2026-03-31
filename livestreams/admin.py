from django.contrib import admin
from .models import LiveSession

@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = ['title', 'vendor', 'platform', 'status', 'scheduled_at', 'is_live', 'is_active']
    list_filter = ['status', 'platform', 'is_active']
    search_fields = ['title', 'vendor__nombre_tienda']
    readonly_fields = ['is_live']
