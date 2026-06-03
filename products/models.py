import io

from django.db import models
from PIL import Image, ImageOps
from vendors.models import Vendor


class Category(models.Model):
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.CASCADE,
        null=True, blank=True, related_name='categories'
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, blank=True)
    description = models.TextField(blank=True, null=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategories'
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['order', 'name']
        unique_together = ['vendor', 'slug']

    def __str__(self):
        return self.name

    @property
    def is_parent(self):
        return self.parent is None


class Product(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    variants = models.JSONField(default=list, blank=True)
    sku = models.CharField(max_length=100, unique=True, blank=True, null=True)
    purchase_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True, verbose_name="Costo de compra"
    )
    shipping_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True, verbose_name="Costo de flete/importación"
    )
    profit_margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True, verbose_name="% de ganancia"
    )
    barcode = models.CharField(
        max_length=50, blank=True, null=True,
        unique=True, verbose_name="Código de barras"
    )
    internal_code = models.CharField(
        max_length=50, blank=True,
        verbose_name="Código interno"
    )
    sell_by = models.JSONField(
        default=list, blank=True,
        verbose_name="Se vende por",
        help_text="Lista de unidades: unidad, qq, lbs, lts"
    )
    is_active = models.BooleanField(default=True)
    is_active_live = models.BooleanField(default=True)
    is_active_pos = models.BooleanField(default=True)
    is_active_web = models.BooleanField(default=True)
    web_is_bestseller = models.BooleanField(
        default=False,
        verbose_name='Destacar en más vendidos (web)',
    )
    web_is_new = models.BooleanField(
        default=False,
        verbose_name='Destacar en nuevos ingresos (web)',
    )
    compare_at_price = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        verbose_name='Precio anterior (web)',
        help_text='Opcional. Si es mayor al precio actual, se muestra % de descuento en la tienda.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def calculated_margin(self):
        """Retorna precio_venta - purchase_cost si ambos existen"""
        if self.price and self.purchase_cost:
            return self.price - self.purchase_cost
        return None


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/images/')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.image:
            return
        try:
            img = Image.open(self.image.path)
            img = ImageOps.exif_transpose(img)
            output = io.BytesIO()
            fmt = img.format or 'JPEG'
            if fmt.upper() == 'JPEG':
                img.save(output, format='JPEG', quality=90, optimize=True)
            else:
                img.save(output, format=fmt)
            output.seek(0)
            with open(self.image.path, 'wb') as f:
                f.write(output.read())
        except Exception:
            pass

    def __str__(self):
        return f'Imagen de {self.product.name}'


class Inventory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='inventories')
    quantity = models.PositiveIntegerField(default=0)
    reserved_quantity = models.PositiveIntegerField(default=0)
    low_stock_alert = models.PositiveIntegerField(default=5)
    purchase_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Costo de compra por unidad"
    )
    precio_venta = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        verbose_name="Precio de venta del lote",
        help_text="Si es null, usa Product.price como fallback",
    )
    almacen = models.ForeignKey(
        'vendors.Almacen', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='inventarios'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inventario'
        verbose_name_plural = 'Inventarios'
        ordering = ['created_at']

    def __str__(self):
        return f'Inventario de {self.product.name}'

    @property
    def available_quantity(self):
        return self.quantity - self.reserved_quantity

    @property
    def is_low_stock(self):
        return self.available_quantity <= self.low_stock_alert


class ProductVariant(models.Model):
    """Variantes estructuradas por talla y color (complementa el JSONField variants de Product)."""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        related_name='variant_objects'
    )
    talla = models.CharField(max_length=20, blank=True)
    color = models.CharField(max_length=30, blank=True)
    color_hex = models.CharField(
        max_length=7, blank=True,
        help_text="Color en formato #RRGGBB para mostrar swatch"
    )
    sku = models.CharField(max_length=100, blank=True, null=True, unique=True)
    stock_extra = models.IntegerField(
        default=0,
        help_text="Stock adicional específico de esta variante (0 = usa el stock del producto)"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Variante de Producto'
        verbose_name_plural = 'Variantes de Producto'
        unique_together = ['product', 'talla', 'color']

    def __str__(self):
        parts = [p for p in [self.talla, self.color] if p]
        return f"{self.product.name} — {' / '.join(parts)}" if parts else self.product.name
