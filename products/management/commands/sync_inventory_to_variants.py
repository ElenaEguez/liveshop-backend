"""
Distribuye Inventory.quantity entre variantes cuando stock_extra=0
pero hay stock físico en Inventory.

Uso:
  python manage.py sync_inventory_to_variants --almacen-id=13
  python manage.py sync_inventory_to_variants --almacen-id=13 --dry-run
  python manage.py sync_inventory_to_variants --almacen-id=13 --vendor-id=2

No modifica Inventory.quantity. Solo actualiza ProductVariant.stock_extra.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from products.models import Inventory, ProductVariant
from products.stock_service import product_has_variants
from vendors.models import Almacen, KardexMovimiento


class Command(BaseCommand):
    help = (
        'Distribuye stock físico (Inventory) en stock_extra de variantes '
        'cuando el catálogo quedó en 0.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--almacen-id',
            type=int,
            required=True,
            help='ID del almacén fuente del stock físico.',
        )
        parser.add_argument(
            '--vendor-id',
            type=int,
            default=None,
            help='Filtrar por vendor.',
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

        inventarios = (
            Inventory.objects.filter(
                almacen_id=almacen_id,
                is_active=True,
                quantity__gt=0,
                product__is_active=True,
                product__vendor_id=effective_vendor_id,
            )
            .select_related('product')
            .order_by('product__name', 'id')
        )

        resumen = {
            'procesados': 0,
            'con_distribucion': 0,
            'skip_stock_extra_ok': 0,
            'skip_sin_variantes': 0,
            'total_variantes_actualizadas': 0,
            'errores': [],
        }
        productos_vistos = set()

        for inv in inventarios.iterator():
            producto = inv.product
            if producto.id in productos_vistos:
                continue
            productos_vistos.add(producto.id)

            if not product_has_variants(producto.id):
                resumen['skip_sin_variantes'] += 1
                continue

            variantes = list(
                ProductVariant.objects.filter(
                    product=producto,
                    is_active=True,
                ).order_by('id')
            )
            num_var = len(variantes)
            if num_var == 0:
                resumen['skip_sin_variantes'] += 1
                continue

            suma_actual = ProductVariant.objects.filter(
                product=producto,
                is_active=True,
            ).aggregate(total=Sum('stock_extra'))['total'] or 0
            suma_actual = int(suma_actual)

            if suma_actual > 0:
                resumen['skip_stock_extra_ok'] += 1
                self.stdout.write(
                    f'  SKIP {producto.name}: ya tiene stock_extra={suma_actual}'
                )
                continue

            total_a_distribuir = int(inv.quantity or 0)
            if total_a_distribuir <= 0:
                continue

            base = total_a_distribuir // num_var
            resto = total_a_distribuir % num_var

            if base == 0 and resto == 0:
                continue

            resumen['procesados'] += 1
            prefix = '[DRY-RUN] ' if dry_run else ''
            self.stdout.write(
                f'  {prefix}{producto.name} | inv.qty={total_a_distribuir} | '
                f'{num_var} variantes'
            )

            plan = []
            for i, variante in enumerate(variantes):
                asignar = base + (1 if i < resto else 0)
                plan.append((variante, asignar))
                self.stdout.write(
                    f'    {variante.talla}/{variante.color}: '
                    f'stock_extra 0 → {asignar}'
                )

            if dry_run:
                resumen['con_distribucion'] += 1
                resumen['total_variantes_actualizadas'] += len(plan)
                continue

            try:
                with transaction.atomic():
                    inv_locked = Inventory.objects.select_for_update().get(pk=inv.pk)
                    for variante, asignar in plan:
                        variante.stock_extra = asignar
                        variante.save(update_fields=['stock_extra'])
                        KardexMovimiento.objects.create(
                            inventory=inv_locked,
                            almacen=almacen,
                            variant=variante,
                            tipo='ajuste',
                            motivo='ajuste_manual',
                            cantidad=asignar,
                            stock_anterior=0,
                            stock_actual=asignar,
                            documento_ref='SYNC-INV-TO-VAR',
                            usuario=None,
                            notas=(
                                f'Sync inv→variant: catálogo 0→{asignar} '
                                f'({variante.talla}/{variante.color}); '
                                f'Inventory sin cambio qty={inv_locked.quantity}'
                            ),
                        )
                resumen['con_distribucion'] += 1
                resumen['total_variantes_actualizadas'] += len(plan)
            except Exception as exc:
                msg = f'{producto.name}: {exc}'
                resumen['errores'].append(msg)
                self.stderr.write(self.style.ERROR(f'  ERROR {msg}'))

        self.stdout.write('\n' + '=' * 40)
        self.stdout.write('=== SYNC INVENTORY → VARIANTS ===')
        self.stdout.write(f'Almacén: {almacen.nombre} (ID={almacen_id})')
        self.stdout.write(
            f'Productos procesados:          {resumen["procesados"]}'
        )
        self.stdout.write(
            f'Con distribución aplicada:     {resumen["con_distribucion"]}'
        )
        self.stdout.write(
            f'Skipped (stock_extra ya ok):   {resumen["skip_stock_extra_ok"]}'
        )
        self.stdout.write(
            f'Skipped (sin variantes):       {resumen["skip_sin_variantes"]}'
        )
        self.stdout.write(
            f'Total variantes actualizadas:  '
            f'{resumen["total_variantes_actualizadas"]}'
        )
        if resumen['errores']:
            self.stdout.write(
                self.style.ERROR(f'Errores: {len(resumen["errores"])}')
            )
            for err in resumen['errores']:
                self.stderr.write(f'  - {err}')
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '\n⚠️  MODO DRY-RUN — ningún cambio fue guardado'
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ Sync completado.'))
