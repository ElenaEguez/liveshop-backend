"""
Alinea stock de variantes con inventario o reconstruye stock_extra desde kardex.

Uso:
  python manage.py reconcile_variant_stock --list-vendors
  python manage.py reconcile_variant_stock --product-name "PRUEBA 6" --dry-run --rebuild-from-kardex
  python manage.py reconcile_variant_stock --rebuild-from-kardex --vendor-id=2
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import Inventory, Product
from products.stock_service import (
    product_has_variants,
    rebuild_variant_stock_from_kardex,
    reconcile_product_variant_stock,
    sum_variant_stock,
)
from vendors.models import Vendor


class Command(BaseCommand):
    help = (
        'Alinea inventario con variantes o reconstruye stock_extra desde movimientos kardex.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--vendor-id', type=int, default=None)
        parser.add_argument('--product-id', type=int, default=None)
        parser.add_argument(
            '--product-name',
            type=str,
            default=None,
            help='Nombre del producto (búsqueda parcial, sin distinguir mayúsculas).',
        )
        parser.add_argument(
            '--list-vendors',
            action='store_true',
            help='Lista vendors (id, tienda) y sale.',
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--rebuild-from-kardex',
            action='store_true',
            help='Recalcula stock_extra por variante sumando cantidades del kardex.',
        )

    def handle(self, *args, **options):
        if options['list_vendors']:
            for v in Vendor.objects.order_by('id'):
                self.stdout.write(f'  id={v.id}  {v.nombre_tienda}  slug={v.slug}')
            return

        vendor_id = options['vendor_id']
        product_id = options['product_id']
        product_name = (options['product_name'] or '').strip()
        dry_run = options['dry_run']
        rebuild = options['rebuild_from_kardex']

        products = Product.objects.filter(is_active=True)
        if vendor_id:
            products = products.filter(vendor_id=vendor_id)
            if not Vendor.objects.filter(pk=vendor_id).exists():
                self.stderr.write(f'Vendor {vendor_id} no existe.')
                self.stderr.write('Use: python manage.py reconcile_variant_stock --list-vendors')
                return
        if product_id:
            products = products.filter(pk=product_id)
            if not products.exists():
                self.stderr.write(f'Producto id={product_id} no encontrado.')
                return
        if product_name:
            products = products.filter(name__icontains=product_name)
            count = products.count()
            if count == 0:
                self.stderr.write(f'Ningún producto coincide con "{product_name}".')
                return
            if count > 1:
                self.stdout.write(f'Varios productos ({count}); se procesan todos:')
                for p in products[:20]:
                    self.stdout.write(f'  id={p.id}  {p.name}  vendor_id={p.vendor_id}')
                if count > 20:
                    self.stdout.write(f'  ... y {count - 20} más')

        updated = 0
        for product in products.iterator():
            if not product_has_variants(product.id):
                continue

            if rebuild:
                if dry_run:
                    from vendors.models import KardexMovimiento
                    from django.db.models import Sum
                    from products.models import ProductVariant

                    for v in ProductVariant.objects.filter(
                        product_id=product.id, is_active=True,
                    ):
                        total = KardexMovimiento.objects.filter(
                            variant_id=v.id,
                        ).aggregate(t=Sum('cantidad'))['t'] or 0
                        nuevo = max(0, int(total))
                        if nuevo != int(v.stock_extra or 0):
                            self.stdout.write(
                                f'[dry-run] {product.name} {v.talla}/{v.color}: '
                                f'stock_extra {v.stock_extra} -> {nuevo}'
                            )
                            updated += 1
                else:
                    with transaction.atomic():
                        cambios = rebuild_variant_stock_from_kardex(product.id)
                    for vid, qty in cambios.items():
                        self.stdout.write(
                            f'{product.name} variante #{vid}: stock_extra = {qty}'
                        )
                    if cambios:
                        updated += 1
                continue

            inv = (
                Inventory.objects.filter(product=product, is_active=True)
                .select_related('almacen')
                .order_by('-quantity', 'id')
                .first()
            )
            if not inv:
                continue

            old = inv.quantity
            new = sum_variant_stock(product.id)
            if old == new:
                continue

            if dry_run:
                self.stdout.write(
                    f'[dry-run] {product.name} @ almacén {inv.almacen_id}: '
                    f'{old} -> {new}'
                )
            else:
                with transaction.atomic():
                    reconcile_product_variant_stock(product.id)
                self.stdout.write(
                    f'{product.name} @ almacén {inv.almacen_id}: {old} -> {new}'
                )
            updated += 1

        accion = 'reconstruidos' if rebuild else 'ajustados'
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Simulación" if dry_run else "Listo"}: {updated} producto(s) {accion}.'
            )
        )
