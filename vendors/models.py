from decimal import Decimal

from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class Vendor(models.Model):
    """Vendor profile model linked to User"""

    INVENTORY_METHOD_CHOICES = [
        ('peps', 'PEPS (Primeros en entrar, primeros en salir)'),
        ('ueps', 'UEPS (Últimos en entrar, primeros en salir)'),
        ('promedio', 'Costo Promedio'),
    ]

    PLAN_CHOICES = [
        ('mensual',    'Mensual - Bs. 300'),
        ('trimestral', 'Trimestral - Bs. 810'),
        ('semestral',  'Semestral - Bs. 1.499'),
    ]

    ESTADO_SUSCRIPCION_CHOICES = [
        ('prueba',     'Prueba gratuita'),
        ('activo',     'Activo'),
        ('vencido',    'Vencido'),
        ('suspendido', 'Suspendido'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='vendor_profile')
    nombre_tienda = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True)
    logo = models.ImageField(upload_to='vendor_logos/', blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    whatsapp = models.CharField(max_length=20, blank=True, null=True)
    tiktok_url = models.URLField(blank=True, null=True)
    facebook_url = models.URLField(blank=True, null=True)
    instagram_url = models.URLField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    payment_qr_image = models.ImageField(upload_to='vendors/qr/', blank=True, null=True)
    payment_instructions = models.TextField(blank=True, null=True)
    accepted_payment_methods = models.CharField(max_length=200, blank=True, null=True)
    moneda = models.CharField(max_length=5, default='Bs.', verbose_name="Moneda de la tienda")
    inventory_method = models.CharField(
        max_length=10,
        choices=INVENTORY_METHOD_CHOICES,
        default='peps',
        verbose_name="Método de valoración de inventario",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    plan = models.CharField(
        max_length=20, choices=PLAN_CHOICES,
        default='mensual', blank=True
    )
    max_usuarios = models.PositiveIntegerField(
        default=3,
        help_text='Número máximo de miembros de equipo permitidos'
    )
    estado_suscripcion = models.CharField(
        max_length=20, choices=ESTADO_SUSCRIPCION_CHOICES,
        default='prueba'
    )
    fecha_vencimiento = models.DateField(null=True, blank=True)
    notas_admin = models.TextField(
        blank=True, default='',
        help_text='Notas internas del superadmin (no visibles para el vendor)'
    )
    transfer_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        verbose_name="Descuento por transferencia (%)",
        help_text="Porcentaje de descuento aplicado al precio web por pago con transferencia. 0 = sin segundo precio.",
    )
    precio_editable = models.BooleanField(
        default=True,
        verbose_name="Precio editable desde panel",
        help_text="Si está desactivado, el precio del producto "
                  "solo se actualiza al recibir órdenes de compra. "
                  "El campo precio no aparece en el formulario de producto.",
    )
    # MODO SIMPLE - vendedores pequeños sin módulos avanzados
    modo_simple = models.BooleanField(
        default=False,
        help_text=(
            'Activo: vendedor usa flujo simplificado. '
            'Sin compras, sin PEPS, sin almacenes múltiples. '
            'Solo activar desde el admin con la acción correspondiente.'
        )
    )

    class Meta:
        verbose_name = 'Vendedor'
        verbose_name_plural = 'Vendedores'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.nombre_tienda
    
    def save(self, *args, **kwargs):
        if not self.pk:
            # New record — generate unique slug from nombre_tienda
            self.slug = self._unique_slug(slugify(self.nombre_tienda))
        else:
            # Existing record — auto-update slug only if nombre_tienda changed
            # and the current slug still matches the old name (not manually set)
            try:
                old = Vendor.objects.get(pk=self.pk)
            except Vendor.DoesNotExist:
                old = None
            if old and old.nombre_tienda != self.nombre_tienda:
                if self.slug == slugify(old.nombre_tienda):
                    self.slug = self._unique_slug(slugify(self.nombre_tienda))
        super().save(*args, **kwargs)

    def _unique_slug(self, base_slug):
        slug = base_slug
        qs = Vendor.objects.exclude(pk=self.pk)
        counter = 2
        while qs.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug


class CustomRole(models.Model):
    """Vendor-defined role with granular module permissions."""
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='custom_roles'
    )
    name = models.CharField(max_length=100)
    # Granular module permissions
    perm_products      = models.BooleanField(default=False, verbose_name='Productos')
    perm_categories    = models.BooleanField(default=False, verbose_name='Categorías')
    perm_compras       = models.BooleanField(default=False, verbose_name='Compras')
    perm_inventory     = models.BooleanField(default=False, verbose_name='Inventario')
    perm_live_sessions = models.BooleanField(default=False, verbose_name='Lives')
    perm_my_store      = models.BooleanField(default=False, verbose_name='Mi Tienda')
    perm_orders        = models.BooleanField(
        default=True,
        help_text='Acceso a pedidos / reservas',
        verbose_name='Pedidos',
    )
    perm_payments      = models.BooleanField(
        default=False,
        help_text='Acceso a pagos y caja',
        verbose_name='Pagos',
    )
    perm_team          = models.BooleanField(default=False, verbose_name='Equipo')
    perm_manage_roles  = models.BooleanField(
        default=False,
        verbose_name='Administrar roles/equipo',
        help_text='Permite crear/editar roles y gestionar miembros del equipo',
    )
    perm_dashboard     = models.BooleanField(default=False, verbose_name='Dashboard')
    perm_pos           = models.BooleanField(default=False, verbose_name='Punto de Venta (POS)')
    perm_warehouse     = models.BooleanField(default=False, verbose_name='Almacén/Transferencias')
    perm_expenses      = models.BooleanField(default=False, verbose_name='Gastos')
    # Permisos granulares (22 módulos de menú)
    perm_arqueos           = models.BooleanField(default=False, verbose_name='Arqueos de caja')
    perm_ventas_pos        = models.BooleanField(default=False, verbose_name='Ventas POS')
    perm_devoluciones      = models.BooleanField(default=False, verbose_name='Devoluciones POS')
    perm_conteos           = models.BooleanField(default=False, verbose_name='Conteo físico')
    perm_conteos_control   = models.BooleanField(default=False, verbose_name='Control de conteos')
    perm_transferencias    = models.BooleanField(default=False, verbose_name='Transferencias')
    perm_almacen           = models.BooleanField(default=False, verbose_name='Almacén / Kardex')
    perm_proveedores       = models.BooleanField(default=False, verbose_name='Proveedores')
    perm_configuracion     = models.BooleanField(default=False, verbose_name='Configuración')
    perm_ecommerce_orders  = models.BooleanField(default=False, verbose_name='Pedidos Web')
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Rol personalizado'
        verbose_name_plural = 'Roles personalizados'
        unique_together = [('vendor', 'name')]

    def __str__(self):
        return f"{self.name} ({self.vendor})"


class TeamMember(models.Model):
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='team_members'
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='team_member_profile'
    )
    custom_role = models.ForeignKey(
        CustomRole, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='members',
        verbose_name='Rol personalizado'
    )
    is_active = models.BooleanField(default=True)
    invited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Miembro de Equipo'
        verbose_name_plural = 'Miembros de Equipo'

    def __str__(self):
        role_name = self.custom_role.name if self.custom_role else 'sin rol'
        return f"{self.user.email} - {role_name} ({self.vendor})"

    def clean(self):
        from django.core.exceptions import ValidationError
        limite = getattr(self.vendor, 'max_usuarios', 3)
        if TeamMember.objects.filter(vendor=self.vendor).exclude(pk=self.pk).count() >= limite:
            raise ValidationError(
                f'Este vendor no puede tener más de {limite} miembros de equipo.'
            )


class Sucursal(models.Model):
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='sucursales'
    )
    nombre = models.CharField(max_length=100)
    direccion = models.TextField(blank=True)
    es_principal = models.BooleanField(default=False)
    activa = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sucursal'
        verbose_name_plural = 'Sucursales'
        unique_together = ['vendor', 'nombre']

    def __str__(self):
        return f"{self.nombre} — {self.vendor.nombre_tienda}"


class Almacen(models.Model):
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE,
        related_name='almacenes'
    )
    nombre = models.CharField(max_length=100)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Almacén'
        verbose_name_plural = 'Almacenes'

    def __str__(self):
        return f"{self.nombre} ({self.sucursal.nombre})"


class KardexMovimiento(models.Model):
    TIPO_CHOICES = [
        ('entrada', 'Entrada'),
        ('salida', 'Salida'),
        ('ajuste', 'Ajuste'),
        ('transferencia', 'Transferencia entre almacenes'),
    ]
    MOTIVO_CHOICES = [
        ('venta', 'Venta'),
        ('venta_live', 'Venta Live'),
        ('compra', 'Compra/Reposición'),
        ('ajuste_manual', 'Ajuste manual'),
        ('devolucion', 'Devolución (venta)'),
        ('devolucion_compra', 'Devolución a proveedor'),
        ('transferencia', 'Transferencia'),
    ]

    inventory = models.ForeignKey(
        'products.Inventory', on_delete=models.CASCADE,
        related_name='movimientos'
    )
    almacen = models.ForeignKey(
        Almacen, on_delete=models.SET_NULL,
        null=True, related_name='movimientos'
    )
    # Variante vendida; null cuando el producto no tiene variantes
    variant = models.ForeignKey(
        'products.ProductVariant',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='kardex_movimientos',
        verbose_name='Variante',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    motivo = models.CharField(max_length=30, choices=MOTIVO_CHOICES)
    cantidad = models.IntegerField()
    stock_anterior = models.IntegerField()
    stock_actual = models.IntegerField()
    costo_promedio = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True
    )
    documento_ref = models.CharField(max_length=50, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de Kardex'
        verbose_name_plural = 'Movimientos de Kardex'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.inventory} ({self.created_at:%Y-%m-%d})"


class Caja(models.Model):
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE,
        related_name='cajas'
    )
    nombre = models.CharField(max_length=50)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Caja'
        verbose_name_plural = 'Cajas'

    def __str__(self):
        return f"{self.sucursal.nombre} — {self.nombre}"


class TurnoCaja(models.Model):
    STATUS_CHOICES = [
        ('abierto', 'Abierto'),
        ('cerrado', 'Cerrado'),
    ]
    caja = models.ForeignKey(
        Caja, on_delete=models.CASCADE,
        related_name='turnos'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='abierto')
    monto_apertura = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    monto_cierre = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    efectivo_esperado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    diferencia_cierre = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    notas_cierre = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Turno de Caja'
        verbose_name_plural = 'Turnos de Caja'
        ordering = ['-fecha_apertura']

    def __str__(self):
        return f"{self.caja} — {self.get_status_display()} ({self.fecha_apertura:%Y-%m-%d %H:%M})"

    @property
    def total_ventas(self):
        from django.db.models import Q
        total = self.ventas.exclude(status='anulada').filter(
            Q(status='completada')
            | Q(es_credito=True, status__in=['credito', 'completada'])
        ).aggregate(total=models.Sum('total'))['total']
        return total or 0

    @property
    def total_ingresos_manuales(self):
        return self.movimientos.filter(tipo='ingreso').aggregate(
            total=models.Sum('monto'))['total'] or 0

    @property
    def total_retiros(self):
        return self.movimientos.filter(tipo='retiro').aggregate(
            total=models.Sum('monto'))['total'] or 0


class MovimientoCaja(models.Model):
    TIPO_CHOICES = [
        ('ingreso', 'Ingreso'),
        ('retiro', 'Retiro'),
    ]
    turno = models.ForeignKey(
        TurnoCaja, on_delete=models.CASCADE,
        related_name='movimientos'
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    concepto = models.CharField(max_length=200)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de Caja'
        verbose_name_plural = 'Movimientos de Caja'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.get_tipo_display()} Bs.{self.monto} — {self.concepto}"


class TicketConfig(models.Model):
    vendor = models.OneToOneField(
        Vendor, on_delete=models.CASCADE,
        related_name='ticket_config'
    )
    mostrar_logo = models.BooleanField(default=True)
    nombre_empresa = models.CharField(max_length=100, blank=True)
    ruc_nit = models.CharField(max_length=20, blank=True)
    direccion = models.TextField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    texto_pie = models.CharField(max_length=200, blank=True, default="¡Gracias por su compra!")
    mostrar_qr = models.BooleanField(default=False)
    moneda = models.CharField(max_length=5, default='Bs.')
    ancho_ticket = models.IntegerField(default=80, help_text="Ancho en mm: 58 o 80")

    class Meta:
        verbose_name = 'Configuración de Ticket'
        verbose_name_plural = 'Configuraciones de Ticket'

    def __str__(self):
        return f"Ticket config — {self.vendor.nombre_tienda}"


class Comprobante(models.Model):
    TIPO_CHOICES = [
        ('factura',         'Factura'),
        ('boleta',          'Boleta'),
        ('nota_credito',    'Nota de Crédito'),
        ('nota_debito',     'Nota de Débito'),
        ('ticket_venta',    'Ticket Venta'),
        ('ticket_compra',   'Ticket Compra'),
    ]
    vendor      = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='comprobantes')
    tipo        = models.CharField(max_length=20, choices=TIPO_CHOICES)
    serie       = models.CharField(max_length=10, default='001', blank=True)
    correlativo = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = 'Comprobante'
        verbose_name_plural = 'Comprobantes'
        unique_together = [('vendor', 'tipo')]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.serie}-{str(self.correlativo).zfill(6)}"


class Promocion(models.Model):
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='promociones'
    )
    titulo = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    imagen = models.ImageField(upload_to='promociones/', null=True, blank=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    activa = models.BooleanField(default=True)
    orden = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Promoción'
        verbose_name_plural = 'Promociones'
        ordering = ['orden', '-fecha_inicio']

    def __str__(self):
        return f"{self.titulo} — {self.vendor.nombre_tienda}"


class TransferenciaAlmacen(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('completada', 'Completada'),
        ('cancelada', 'Cancelada'),
    ]

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='transferencias')
    almacen_origen = models.ForeignKey(
        'Almacen', on_delete=models.PROTECT,
        related_name='transferencias_salida')
    almacen_destino = models.ForeignKey(
        'Almacen', on_delete=models.PROTECT,
        related_name='transferencias_entrada')
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='pendiente')
    notas = models.TextField(blank=True, default='')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='transferencias_creadas')
    completado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='transferencias_completadas')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Transferencia de Almacén'
        verbose_name_plural = 'Transferencias de Almacén'

    def __str__(self):
        return (f'Transferencia {self.almacen_origen} → '
                f'{self.almacen_destino} [{self.estado}]')


class TransferenciaAlmacenItem(models.Model):
    transferencia = models.ForeignKey(
        TransferenciaAlmacen,
        on_delete=models.CASCADE,
        related_name='items')
    producto = models.ForeignKey(
        'products.Product',
        on_delete=models.PROTECT,
        related_name='transferencia_items')
    variante = models.ForeignKey(
        'products.ProductVariant',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='transferencia_items')
    cantidad = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = 'Ítem de Transferencia'
        verbose_name_plural = 'Ítems de Transferencia'

    def __str__(self):
        return f'{self.producto.name} x{self.cantidad}'


class ConteoFisico(models.Model):
    ESTADO_CHOICES = [
        ('abierto',   'Abierto — en progreso'),
        ('cerrado',   'Cerrado — pendiente aprobación'),
        ('aprobado',  'Aprobado — ajustes aplicados'),
        ('cancelado', 'Cancelado'),
    ]

    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE,
        related_name='conteos_fisicos')
    almacen = models.ForeignKey(
        'Almacen', on_delete=models.PROTECT,
        related_name='conteos_fisicos')
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='abierto')
    fecha = models.DateField()
    notas = models.TextField(blank=True, default='')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True,
        related_name='conteos_creados')
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='conteos_aprobados')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Conteo Físico'
        verbose_name_plural = 'Conteos Físicos'

    def __str__(self):
        return (f'Conteo {self.almacen.nombre} '
                f'{self.fecha} [{self.estado}]')


class ConteoFisicoItem(models.Model):
    conteo = models.ForeignKey(
        ConteoFisico, on_delete=models.CASCADE,
        related_name='items')
    producto = models.ForeignKey(
        'products.Product',
        on_delete=models.PROTECT,
        related_name='conteo_items')
    variante = models.ForeignKey(
        'products.ProductVariant',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='conteo_items')
    stock_sistema = models.IntegerField(
        default=0,
        help_text='Stock en sistema al momento del conteo')
    stock_fisico = models.IntegerField(
        default=0,
        help_text='Stock contado físicamente')
    diferencia = models.IntegerField(
        default=0,
        help_text='stock_fisico - stock_sistema (calculado)')
    contado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True,
        related_name='items_contados')
    notas = models.CharField(
        max_length=300, blank=True, default='')

    class Meta:
        verbose_name = 'Ítem de Conteo'
        verbose_name_plural = 'Ítems de Conteo'
        unique_together = [('conteo', 'producto', 'variante')]

    def save(self, *args, **kwargs):
        self.diferencia = self.stock_fisico - self.stock_sistema
        super().save(*args, **kwargs)

    def __str__(self):
        return (f'{self.producto.name} — '
                f'sistema:{self.stock_sistema} '
                f'físico:{self.stock_fisico} '
                f'dif:{self.diferencia}')
