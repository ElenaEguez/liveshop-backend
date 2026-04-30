from django.db import models


class VendorWebsite(models.Model):
    THEME_CHOICES = [
        ('default', 'Default'),
        ('minimal', 'Minimal'),
        ('bold', 'Bold'),
    ]
    vendor = models.OneToOneField(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='website'
    )
    is_published = models.BooleanField(default=False)
    theme = models.CharField(max_length=50, choices=THEME_CHOICES, default='default')
    custom_css = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Sitio Web'
        verbose_name_plural = 'Sitios Web'

    def __str__(self):
        return f"Sitio de {self.vendor.nombre_tienda}"


class WebsitePage(models.Model):
    website = models.ForeignKey(
        VendorWebsite, on_delete=models.CASCADE,
        related_name='pages'
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField()
    content = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Página'
        verbose_name_plural = 'Páginas'
        ordering = ['order']
        unique_together = ['website', 'slug']

    def __str__(self):
        return f"{self.title} — {self.website}"


class WebsiteBanner(models.Model):
    website = models.ForeignKey(
        VendorWebsite, on_delete=models.CASCADE,
        related_name='banners'
    )
    image = models.ImageField(upload_to='website/banners/')
    title = models.CharField(max_length=200, blank=True)
    link = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Banner'
        verbose_name_plural = 'Banners'
        ordering = ['order']

    def __str__(self):
        return f"{self.title or 'Banner'} — {self.website}"


class CartOrder(models.Model):
    DELIVERY_CHOICES = [
        ('pickup', 'Recoger en tienda'),
        ('delivery', 'Envío a domicilio'),
        ('envio_nacional', 'Envío nacional'),
        ('envio_scz', 'Envío Departamento SCZ'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('pending_confirmation', 'Pendiente de confirmación'),
        ('confirmed', 'Confirmado'),
        ('cancelled', 'Cancelado'),
        ('delivered', 'Entregado'),
    ]
    PAYMENT_CHOICES = [
        ('tigo_money', 'Tigo Money'),
        ('banco_union', 'Banco Unión'),
        ('efectivo', 'Efectivo'),
    ]

    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='cart_orders'
    )
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.CharField(max_length=254, blank=True)
    customer_address = models.TextField(blank=True)
    delivery_method = models.CharField(max_length=20, choices=DELIVERY_CHOICES)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='pending')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES)
    payment_receipt = models.ImageField(upload_to='website/receipts/', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pedido'
        verbose_name_plural = 'Pedidos'
        ordering = ['-created_at']

    def __str__(self):
        return f"Pedido #{self.pk} — {self.customer_name} ({self.get_status_display()})"


class CartOrderItem(models.Model):
    order = models.ForeignKey(
        CartOrder, on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        'products.Product', on_delete=models.CASCADE
    )
    variant_id = models.IntegerField(null=True, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Ítem de Pedido'
        verbose_name_plural = 'Ítems de Pedido'

    def __str__(self):
        return f"{self.product.name} x{self.quantity} — Pedido #{self.order.pk}"
