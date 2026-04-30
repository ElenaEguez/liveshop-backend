from django.core.management.base import BaseCommand
from products.models import Category
from vendors.models import Vendor


class Command(BaseCommand):
    help = 'Asigna el vendor correcto a las categorías que tienen vendor=None'

    def add_arguments(self, parser):
        parser.add_argument(
            '--vendor-id',
            type=int,
            help='ID del vendor al que asignar las categorías huérfanas (omitir si solo hay uno)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se haría sin hacer cambios',
        )

    def handle(self, *args, **options):
        orphans = Category.objects.filter(vendor__isnull=True)
        count = orphans.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('No hay categorías sin vendor. Todo OK.'))
            return

        self.stdout.write(f'Categorías sin vendor: {count}')
        for cat in orphans:
            self.stdout.write(f'  id={cat.id}  name="{cat.name}"  slug="{cat.slug}"')

        vendor_id = options.get('vendor_id')
        if vendor_id:
            try:
                vendor = Vendor.objects.get(pk=vendor_id)
            except Vendor.DoesNotExist:
                self.stderr.write(self.style.ERROR(f'No existe un vendor con id={vendor_id}'))
                return
        else:
            vendors = Vendor.objects.all()
            if vendors.count() == 1:
                vendor = vendors.first()
                self.stdout.write(f'Un solo vendor en el sistema: "{vendor}" (id={vendor.pk})')
            else:
                self.stderr.write(
                    self.style.ERROR(
                        'Hay más de un vendor. Indica cuál usar con --vendor-id=<id>.\n'
                        'Vendors disponibles:'
                    )
                )
                for v in vendors:
                    self.stderr.write(f'  id={v.pk}  nombre="{v.nombre_tienda}"')
                return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'[DRY RUN] Se asignaría vendor="{vendor}" a {count} categorías.'
            ))
            return

        updated = orphans.update(vendor=vendor)
        self.stdout.write(self.style.SUCCESS(
            f'Asignado vendor="{vendor}" a {updated} categorías.'
        ))
