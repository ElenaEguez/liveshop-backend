"""
Alinea Inventory.quantity con la suma de stock_extra por producto (con variantes).

Solo ajusta el almacén con mayor stock por producto (evita duplicar el total en cada almacén).

Uso:
  python manage.py reconcile_variant_stock --vendor-id=1
  python manage.py reconcile_variant_stock --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import Inventory, Product
from products.stock_service import (
    product_has_variants,
    reconcile_product_variant_stock,
    sum_variant_stock,
)
from vendors.models import Vendor


class Command(BaseCommand):
    help = 'Alinea inventario (un almacén por producto) con la suma de stock por variante.'

    def add_arguments(self, parser):
        parser.add_argument('--vendor-id', type=int, default=None)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        vendor_id = options['vendor_id']
        dry_run = options['dry_run']

        products = Product.objects.filter(is_active=True)
        if vendor_id:
            products = products.filter(vendor_id=vendor_id)
            if not Vendor.objects.filter(pk=vendor_id).exists():
                self.stderr.write(f'Vendor {vendor_id} no existe.')
                return

        updated = 0
        for product in products.iterator():
            if not product_has_variants(product.id):
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

        self.stdout.write(
            self.style.SUCCESS(
                f'{"Simulación" if dry_run else "Listo"}: {updated} producto(s) ajustados.'
            )
        )
