from django.db import models
from livestreams.models import LiveSession
from products.models import Product

class Reservation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('confirmed', 'Confirmada'),
        ('shipped', 'Enviada'),
        ('recibido', 'Recibido'),
        ('delivered', 'Entregada'),
        ('cancelled', 'Cancelada'),
        ('paid', 'Pagada'),
    ]

    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name='reservations')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reservations')
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reservations'
    )
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=20)
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, null=True)
    variant_detail = models.CharField(max_length=100, blank=True, default='')
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cupon = models.ForeignKey(
        'payments.Cupon', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reservations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reserva'
        verbose_name_plural = 'Reservas'
        ordering = ['-created_at']

    def __str__(self):
        return f'Reserva de {self.customer_name} - {self.product.name}'

    @property
    def total_price(self):
        from decimal import Decimal
        subtotal = self.quantity * self.product.price
        return max(subtotal - (self.descuento or Decimal('0')), Decimal('0'))
