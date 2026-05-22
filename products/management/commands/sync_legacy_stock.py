"""
Sincroniza stock legacy: crea/actualiza filas Inventory para productos
con variantes cuyo stock_extra > 0 pero sin Inventory suficiente en el almacén.

Uso:
  python manage.py sync_legacy_stock --almacen-id=<ID> [--dry-run] [--vendor-id=<ID>]

Agrupa por producto: Inventory.quantity = suma(stock_extra) de variantes activas.
No modifica stock_extra. Kardex motivo sync_legacy por producto.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from products.models import Inventory, Product, ProductVariant
from products.stock_service import StockError, apply_stock_delta, product_has_variants
from vendors.models import Almacen


class Command(BaseCommand):
    help = (
        'Crea/actualiza Inventory desde suma de stock_extra legacy (por producto).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--almacen-id',
            type=int,
            required=True,
            help='ID del almacén destino para el sync.',
        )
        parser.add_argument(
            '--vendor-id',
            type=int,
            default=None,
            help='Filtrar productos por vendor.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo muestra qué haría, sin guardar.',
        )

    def handle(self, *args, **options):
        almacen_id = options['almacen_id']
        vendor_id = options['vendor_id']
        dry_run = options['dry_run']

        try:
            almacen = Almacen.objects.select_related('sucursal').get(pk=almacen_id)
        except Almacen.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Almacén ID {almacen_id} no encontrado'))
            return

        effective_vendor_id = vendor_id or almacen.sucursal.vendor_id
        if vendor_id is not None and almacen.sucursal.vendor_id != vendor_id:
            self.stderr.write(
                self.style.ERROR(
                    f'El almacén {almacen_id} pertenece al vendor '
                    f'{almacen.sucursal.vendor_id}, no al vendor {vendor_id}.'
                )
            )
            return

        productos = Product.objects.filter(
            vendor_id=effective_vendor_id,
            is_active=True,
            variants__is_active=True,
            variants__stock_extra__gt=0,
        ).distinct().order_by('name', 'id')

        resumen = {
            'procesados': 0,
            'con_delta': 0,
            'skipped': 0,
            'total_unidades': 0,
            'errores': [],
        }

        for producto in productos.iterator():
            suma_stock_extra = ProductVariant.objects.filter(
                product=producto,
                is_active=True,
                stock_extra__gt=0,
            ).aggregate(total=Sum('stock_extra'))['total'] or 0
            suma_stock_extra = int(suma_stock_extra)

            if suma_stock_extra <= 0:
                resumen['skipped'] += 1
                continue

            inv = Inventory.objects.filter(
                product=producto,
                almacen_id=almacen_id,
                is_active=True,
            ).first()

            inventory_actual = int(inv.quantity if inv else 0)

            if inventory_actual >= suma_stock_extra:
                resumen['skipped'] += 1
                self.stdout.write(
                    f'  SKIP {producto.name}: '
                    f'Inventory={inventory_actual} >= '
                    f'stock_extra_total={suma_stock_extra}'
                )
                continue

            delta = suma_stock_extra - inventory_actual

            self.stdout.write(
                f'  {"[DRY-RUN] " if dry_run else ""}'
                f'{producto.name}: '
                f'Inventory={inventory_actual} → {suma_stock_extra} '
                f'(delta=+{delta})'
            )

            if not dry_run:
                try:
                    variant_ref = None
                    if product_has_variants(producto.id):
                        variant_ref = (
                            ProductVariant.objects.filter(
                                product=producto,
                                is_active=True,
                                stock_extra__gt=0,
                            )
                            .order_by('id')
                            .first()
                        )
                    with transaction.atomic():
                        apply_stock_delta(
                            product=producto,
                            almacen=almacen,
                            delta=delta,
                            variant=variant_ref,
                            usuario=None,
                            motivo='sync_legacy',
                            documento_ref='SYNC-LEGACY',
                            notas=(
                                f'Sync legacy: suma stock_extra={suma_stock_extra} '
                                f'variantes activas, '
                                f'inventory_anterior={inventory_actual}'
                            ),
                            create_kardex=True,
                            sync_variant_with_inventory=False,
                        )
                    resumen['con_delta'] += 1
                    resumen['total_unidades'] += delta
                except (StockError, Exception) as exc:
                    msg = f'{producto.name}: {exc}'
                    resumen['errores'].append(msg)
                    self.stderr.write(self.style.ERROR(f'  ERROR {msg}'))
            else:
                resumen['con_delta'] += 1
                resumen['total_unidades'] += delta

            resumen['procesados'] += 1

        self.stdout.write('')
        self.stdout.write('=== SYNC LEGACY STOCK ===')
        self.stdout.write(f'Almacén: {almacen.nombre} (ID={almacen_id})')
        self.stdout.write(f'Productos procesados: {resumen["procesados"]}')
        self.stdout.write(f'Con delta aplicado:   {resumen["con_delta"]}')
        self.stdout.write(f'Skipped (ya en sync): {resumen["skipped"]}')
        self.stdout.write(f'Total uds creadas:    {resumen["total_unidades"]}')
        if resumen['errores']:
            self.stdout.write(f'Errores: {len(resumen["errores"])}')
            for err in resumen['errores']:
                self.stderr.write(f'  - {err}')
        if dry_run:
            self.stdout.write(self.style.WARNING(
                'MODO DRY-RUN — ningún cambio fue guardado'
            ))
