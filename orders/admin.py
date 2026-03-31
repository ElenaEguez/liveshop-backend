from django.contrib import admin
from .models import Reservation

@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ['customer_name', 'session', 'product', 'quantity', 'status', 'total_price', 'created_at']
    list_filter = ['status', 'session__platform']
    search_fields = ['customer_name', 'customer_phone', 'product__name']
    readonly_fields = ['total_price']
