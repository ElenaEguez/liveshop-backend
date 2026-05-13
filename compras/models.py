from django.db import models
from django.conf import settings
from vendors.models import Vendor, Sucursal, Almacen
from products.models import Product, ProductVariant


class Proveedor(models.Model):
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='proveedores'
    )
    nombre = models.CharField(max_length=200)
    contacto = models.CharField(max_length=200, blank=True, default='')
    telefono = models.CharField(max_length=30, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    notas = models.TextField(blank=True, default='')
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'

    def __str__(self):
        return self.nombre


class OrdenCompra(models.Model):
    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('pendiente', 'Pendiente'),
        ('recibida', 'Recibida'),
        ('cancelada', 'Cancelada'),
    ]

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='ordenes_compra'
    )
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    almacen = models.ForeignKey(
        Almacen, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    numero = models.CharField(max_length=20, unique=True)
    factura_compra = models.CharField(max_length=80, blank=True, default='')
    fecha = models.DateField()
    fecha_entrega = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES,
        default='borrador'
    )
    notas = models.TextField(blank=True, default='')
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    descuento = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True,
        related_name='ordenes_compra_creadas'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Orden de Compra'
        verbose_name_plural = 'Órdenes de Compra'

    def __str__(self):
        return f'OC-{self.numero}'

    def save(self, *args, **kwargs):
        # Auto-número por vendor
        if not self.pk and not self.numero:
            numeros = OrdenCompra.objects.filter(
                vendor=self.vendor
            ).values_list('numero', flat=True)
            max_numero = 0
            for numero in numeros:
                numero_txt = str(numero or '').strip()
                if numero_txt.isdigit():
                    max_numero = max(max_numero, int(numero_txt))
            n = max_numero + 1
            self.numero = str(n).zfill(6)
        super().save(*args, **kwargs)

    def recalcular_totales(self):
        from django.db.models import Sum
        subtotal = self.items.aggregate(
            total=Sum('subtotal')
        )['total'] or 0
        self.subtotal = subtotal
        self.total = subtotal - self.descuento
        self.save(update_fields=['subtotal', 'total'])


class OrdenCompraItem(models.Model):
    orden = models.ForeignKey(
        OrdenCompra, on_delete=models.CASCADE,
        related_name='items'
    )
    producto = models.ForeignKey(
        Product, on_delete=models.PROTECT,
        related_name='compra_items'
    )
    variante = models.ForeignKey(
        ProductVariant, on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='compra_items'
    )
    almacen = models.ForeignKey(
        'vendors.Almacen',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='compra_items',
        help_text='Almacén destino de este ítem específico'
    )
    costo_mercaderia = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Costo base de la mercadería por unidad'
    )
    flete_unitario = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Costo de flete por unidad'
    )
    costo_unitario_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='costo_mercaderia + flete_unitario (calculado)'
    )
    porcentaje_ganancia = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text='% de ganancia sobre costo unitario total'
    )
    precio_venta_sugerido = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Precio de venta calculado (costo * (1 + %ganancia/100))'
    )
    descripcion = models.CharField(
        max_length=300, blank=True, default=''
    )
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(
        max_digits=12, decimal_places=2
    )
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = 'Ítem de Compra'
        verbose_name_plural = 'Ítems de Compra'

    def save(self, *args, **kwargs):
        # Costo unitario total
        self.costo_unitario_total = (
            self.costo_mercaderia + self.flete_unitario
        )
        # Precio de venta sugerido
        if self.porcentaje_ganancia > 0:
            self.precio_venta_sugerido = self.costo_unitario_total * (
                1 + self.porcentaje_ganancia / 100
            )
        else:
            self.precio_venta_sugerido = self.costo_unitario_total
        # Subtotal basado en precio_unitario (lo que se pagó)
        self.subtotal = self.cantidad * self.precio_unitario
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.producto} x{self.cantidad}'


class DevolucionCompra(models.Model):
    """
    Devolución de mercadería al proveedor: descuenta inventario por almacén
    y stock de variante (si aplica), registrando salida en kardex.
    """
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='devoluciones_compra',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='devoluciones_compra_creadas',
    )
    documento_ref = models.CharField(
        max_length=120, blank=True, default='',
        help_text='Nota de remisión, guía u otro documento del proveedor',
    )
    notas = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Devolución a proveedor'
        verbose_name_plural = 'Devoluciones a proveedor'

    def __str__(self):
        return f'Devolución compra #{self.pk} — {self.vendor}'


class DevolucionCompraItem(models.Model):
    devolucion = models.ForeignKey(
        DevolucionCompra, on_delete=models.CASCADE,
        related_name='items',
    )
    producto = models.ForeignKey(
        Product, on_delete=models.PROTECT,
        related_name='devolucion_compra_items',
    )
    variante = models.ForeignKey(
        ProductVariant, on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='devolucion_compra_items',
    )
    almacen = models.ForeignKey(
        Almacen, on_delete=models.PROTECT,
        related_name='devolucion_compra_items',
    )
    cantidad = models.PositiveIntegerField()

    class Meta:
        verbose_name = 'Ítem devolución a proveedor'
        verbose_name_plural = 'Ítems devolución a proveedor'

    def __str__(self):
        return f'{self.producto} x{self.cantidad}'
