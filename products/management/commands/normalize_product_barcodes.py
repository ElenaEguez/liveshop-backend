from django.core.management.base import BaseCommand

from products.barcode_utils import is_valid_ean13, normalize_barcode_value
from products.models import Product


class Command(BaseCommand):
    help = (
        'Asigna EAN-13 válido a productos cuyo barcode está vacío, '
        'es alfanumérico (SKU) o tiene dígito de control incorrecto.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra cambios sin guardar en la base de datos.',
        )
        parser.add_argument(
            '--vendor-id',
            type=int,
            help='Limitar a un vendor (opcional).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        qs = Product.objects.all().order_by('id')
        vendor_id = options.get('vendor_id')
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)

        updated = 0
        skipped = 0

        for product in qs:
            current = (product.barcode or '').strip()
            if is_valid_ean13(current):
                skipped += 1
                continue

            others = Product.objects.exclude(pk=product.pk)
            new_code = normalize_barcode_value(current or None, existing_products=others)

            self.stdout.write(
                f'  id={product.id} "{product.name}" '
                f'{current or "(vacío)"} -> {new_code}'
            )
            if not dry_run:
                product.barcode = new_code
                product.save(update_fields=['barcode'])
            updated += 1

        verb = 'Se actualizarían' if dry_run else 'Actualizados'
        self.stdout.write(
            self.style.SUCCESS(
                f'{verb} {updated} producto(s). '
                f'{skipped} ya tenían EAN-13 válido.'
            )
        )
