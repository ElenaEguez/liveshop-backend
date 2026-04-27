from django.db import models
from django.conf import settings
from orders.models import Reservation

class Payment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('tigo_money', 'Tigo Money'),
        ('banco_union', 'Banco Unión'),
        ('qr', 'QR'),
        ('cash', 'Efectivo'),
        ('other', 'Otro'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('submitted', 'Presentado'),
        ('confirmed', 'Confirmado'),
        ('rejected', 'Rechazado'),
    ]

    reservation = models.OneToOneField(Reservation, on_delete=models.CASCADE, related_name='payment')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    receipt_image = models.ImageField(upload_to='payments/receipts/', blank=True, null=True)
    customer_reference = models.CharField(max_length=100, blank=True, null=True)
    vendor_notes = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'
        ordering = ['-created_at']

    def __str__(self):
        return f'Pago de {self.reservation.customer_name} - {self.status}'


class MetodoPago(models.Model):
    TIPO_CHOICES = [
        ('efectivo', 'Efectivo'),
        ('qr', 'QR / Transferencia'),
        ('tarjeta', 'Tarjeta'),
        ('credito', 'Crédito'),
        ('mixto', 'Mixto'),
        ('otro', 'Otro'),
    ]
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='metodos_pago'
    )
    nombre = models.CharField(max_length=50)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    icono = models.CharField(max_length=50, blank=True, default='payments')
    activo = models.BooleanField(default=True)
    orden = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Método de Pago'
        verbose_name_plural = 'Métodos de Pago'
        ordering = ['orden', 'nombre']

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"


class Cupon(models.Model):
    TIPO_CHOICES = [
        ('porcentaje', 'Porcentaje de descuento'),
        ('monto_fijo', 'Monto fijo de descuento'),
    ]
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='cupones'
    )
    codigo = models.CharField(max_length=20, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    usos_maximos = models.IntegerField(null=True, blank=True)
    usos_actuales = models.IntegerField(default=0)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    aplica_live = models.BooleanField(default=True)
    aplica_pos = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Cupón'
        verbose_name_plural = 'Cupones'

    def __str__(self):
        return f"{self.codigo} — {self.get_tipo_display()} ({self.valor})"


class CategoriaGasto(models.Model):
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='categorias_gasto'
    )
    nombre = models.CharField(max_length=50)

    class Meta:
        verbose_name = 'Categoría de Gasto'
        verbose_name_plural = 'Categorías de Gasto'
        unique_together = ['vendor', 'nombre']

    def __str__(self):
        return self.nombre


class VentaPOS(models.Model):
    STATUS_CHOICES = [
        ('completada', 'Completada'),
        ('anulada', 'Anulada'),
        ('credito', 'Venta a crédito'),
    ]
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='ventas_pos'
    )
    sucursal = models.ForeignKey(
        'vendors.Sucursal', on_delete=models.SET_NULL,
        null=True, related_name='ventas'
    )
    caja = models.ForeignKey(
        'vendors.Caja', on_delete=models.SET_NULL,
        null=True, related_name='ventas'
    )
    turno = models.ForeignKey(
        'vendors.TurnoCaja', on_delete=models.SET_NULL,
        null=True, related_name='ventas'
    )
    numero_ticket = models.CharField(max_length=20)
    cliente_nombre = models.CharField(max_length=100, default='Genérico')
    cliente_telefono = models.CharField(max_length=20, blank=True)
    metodo_pago = models.ForeignKey(
        MetodoPago, on_delete=models.SET_NULL,
        null=True
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text="Porcentaje aplicado (ej: 10.00 = 10%)"
    )
    discount_type = models.CharField(
        max_length=10,
        choices=[('PERCENT', 'Porcentaje'), ('FIXED', 'Monto fijo')],
        default='FIXED',
        null=True, blank=True
    )
    CANAL_CHOICES = [
        ('TIENDA', 'Tienda física'),
        ('LIVE', 'Live streaming'),
        ('WEB', 'Tienda web'),
        ('DOMICILIO', 'Envío a domicilio'),
        ('INTERPROVINCIAL', 'Envío interprovincial'),
        ('NACIONAL', 'Envío nacional'),
    ]
    canal_venta = models.CharField(
        max_length=20,
        choices=CANAL_CHOICES,
        default='TIENDA'
    )
    direccion_envio = models.TextField(
        null=True, blank=True,
        help_text="Dirección para canales de envío"
    )
    total = models.DecimalField(max_digits=10, decimal_places=2)
    monto_recibido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    vuelto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cupon = models.ForeignKey(
        Cupon, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completada')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    es_credito = models.BooleanField(default=False)
    plazo_dias = models.IntegerField(null=True, blank=True)
    fecha_vencimiento_credito = models.DateField(null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Venta POS'
        verbose_name_plural = 'Ventas POS'
        ordering = ['-created_at']

    def __str__(self):
        return f"Ticket {self.numero_ticket} — {self.cliente_nombre} ({self.total})"


class VentaPOSItem(models.Model):
    venta = models.ForeignKey(
        VentaPOS, on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        'products.Product', on_delete=models.SET_NULL,
        null=True
    )
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    cantidad = models.IntegerField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Ítem de Venta POS'
        verbose_name_plural = 'Ítems de Venta POS'

    def __str__(self):
        return f"{self.product} x{self.cantidad} — {self.venta.numero_ticket}"


class GastoOperativo(models.Model):
    STATUS_CHOICES = [
        ('activo', 'Activo'),
        ('anulado', 'Anulado'),
    ]
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        related_name='gastos'
    )
    sucursal = models.ForeignKey(
        'vendors.Sucursal', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    categoria = models.ForeignKey(
        CategoriaGasto, on_delete=models.SET_NULL,
        null=True
    )
    concepto = models.CharField(max_length=200)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='activo')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Gasto Operativo'
        verbose_name_plural = 'Gastos Operativos'
        ordering = ['-fecha', '-created_at']

    def __str__(self):
        return f"{self.concepto} — {self.monto} ({self.fecha})"


class PagoCredito(models.Model):
    """Registra cada pago parcial o total de una venta a crédito."""
    venta = models.ForeignKey(
        VentaPOS, on_delete=models.CASCADE,
        related_name='pagos_credito'
    )
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo_pago = models.ForeignKey(
        MetodoPago, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    notas = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pago de Crédito'
        verbose_name_plural = 'Pagos de Crédito'
        ordering = ['created_at']

    def __str__(self):
        return f"Pago {self.monto} — {self.venta.numero_ticket}"
