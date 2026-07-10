from django.core.management.base import BaseCommand
from django.db.models import Count

from products.models import Product


class Command(BaseCommand):
    help = 'Lista productos que comparten el mismo codigo interno (internal_code).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--vendor-id',
            type=int,
            default=None,
            help='Filtrar por vendor_id',
        )

    def handle(self, *args, **options):
        qs = Product.objects.exclude(internal_code='').exclude(internal_code__isnull=True)
        if options['vendor_id']:
            qs = qs.filter(vendor_id=options['vendor_id'])

        dups = (
            qs.values('internal_code')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .order_by('-cnt', 'internal_code')
        )

        total = dups.count()
        self.stdout.write(f'=== CODIGOS INTERNOS DUPLICADOS ===')
        self.stdout.write(f'Total codigos duplicados: {total}\n')

        for row in dups:
            code = row['internal_code']
            self.stdout.write(f"Codigo: {code} ({row['cnt']} productos)")
            products = (
                Product.objects.filter(internal_code=code)
                .select_related('vendor')
                .order_by('id')
            )
            if options['vendor_id']:
                products = products.filter(vendor_id=options['vendor_id'])
            for p in products:
                vendor = p.vendor.name if p.vendor_id else p.vendor_id
                self.stdout.write(
                    f'  ID={p.id} | {p.name[:70]} | barcode={p.barcode} | '
                    f'sku={p.sku} | vendor={vendor}'
                )
            self.stdout.write('')
