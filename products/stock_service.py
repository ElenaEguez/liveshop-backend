"""
Movimientos de stock unificados: Inventory (por almacén) + ProductVariant.stock_extra + Kardex.

Reglas:
- Con variante: el mismo delta se aplica a stock_extra y a Inventory del almacén.
- Sin variante: solo Inventory (productos sin variantes activas).
- Transferencias entre almacenes: solo Inventory; stock_extra no cambia (total global por variante).
- Kardex cantidad con signo: positivo = entrada, negativo = salida.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import Inventory, Product, ProductVariant

logger = logging.getLogger(__name__)


class StockError(ValueError):
    """Stock insuficiente o datos inválidos."""


def product_has_variants(product_id: int) -> bool:
    return ProductVariant.objects.filter(
        product_id=product_id, is_active=True,
    ).exists()


def product_has_variant_draft(product_id: int) -> bool:
    """Variantes en JSON del producto aún sin variantes activas en BD."""
    if product_has_variants(product_id):
        return False
    try:
        raw = Product.objects.values_list('variants', flat=True).get(pk=product_id)
    except Product.DoesNotExist:
        return False
    if not isinstance(raw, list) or not raw:
        return False
    for row in raw:
        if not isinstance(row, dict):
            continue
        talla = (row.get('size') or row.get('talla') or '').strip()
        color = (row.get('color') or '').strip()
        if talla or color:
            return True
    return len(raw) > 0


def kardex_product_visible(product_id: int) -> bool:
    """Ocultar kardex de productos en borrador de variantes (sin aprobar/sincronizar)."""
    return not product_has_variant_draft(product_id)


def _inventory_rows_for_lote(product: Product, almacen, *, lock: bool = False):
    """Filas de inventario para producto + almacén (incluye duplicados legacy)."""
    qs = Inventory.objects.filter(product=product, almacen=almacen).order_by(
        'created_at', 'id',
    )
    if lock:
        ids = list(qs.values_list('pk', flat=True))
        if not ids:
            return []
        return list(
            Inventory.objects.select_for_update().filter(pk__in=ids).order_by(
                'created_at', 'id',
            )
        )
    return list(qs)


def _merge_duplicate_inventories(rows: list[Inventory]) -> Inventory:
    """
    Consolida lotes duplicados (mismo producto + almacén) en el registro más antiguo.
    Los duplicados se desactivan con cantidad 0 para no romper kardex histórico.
    """
    if not rows:
        raise StockError('No hay inventario para consolidar.')
    primary = rows[0]
    if len(rows) == 1:
        return primary

    total_qty = sum(int(r.quantity or 0) for r in rows)
    total_reserved = sum(int(r.reserved_quantity or 0) for r in rows)

    for row in reversed(rows):
        if row.purchase_cost is not None:
            primary.purchase_cost = row.purchase_cost
        if row.precio_venta is not None:
            primary.precio_venta = row.precio_venta

    primary.quantity = total_qty
    primary.reserved_quantity = total_reserved
    primary.is_active = True
    primary.save(update_fields=[
        'quantity', 'reserved_quantity', 'purchase_cost', 'precio_venta',
        'is_active', 'updated_at',
    ])

    dup_ids = []
    for dup in rows[1:]:
        dup.quantity = 0
        dup.reserved_quantity = 0
        dup.is_active = False
        dup.save(update_fields=['quantity', 'reserved_quantity', 'is_active', 'updated_at'])
        dup_ids.append(dup.pk)

    logger.warning(
        'Inventario duplicado consolidado: product_id=%s almacen_id=%s '
        'primary_id=%s merged_ids=%s total_qty=%s',
        primary.product_id,
        primary.almacen_id,
        primary.pk,
        dup_ids,
        total_qty,
    )
    return primary


def get_or_create_inventory(
    product: Product,
    almacen,
    *,
    lock: bool = False,
    defaults: dict | None = None,
) -> Inventory:
    base_defaults = {
        'quantity': 0,
        'reserved_quantity': 0,
        'is_active': True,
    }
    if defaults:
        base_defaults.update(defaults)

    rows = _inventory_rows_for_lote(product, almacen, lock=lock)
    if rows:
        return _merge_duplicate_inventories(rows)

    return Inventory.objects.create(
        product=product,
        almacen=almacen,
        **base_defaults,
    )


def get_system_stock(product_id: int, almacen_id, variant_id=None) -> int:
    """Stock en sistema antes de un movimiento."""
    if variant_id:
        v = ProductVariant.objects.get(pk=variant_id, product_id=product_id)
        return max(0, int(v.stock_extra or 0))
    inv = Inventory.objects.filter(
        product_id=product_id, almacen_id=almacen_id, is_active=True,
    ).first()
    return max(0, int(inv.quantity if inv else 0))


def kardex_tipo_from_delta(delta: int) -> str:
    if delta > 0:
        return 'entrada'
    if delta < 0:
        return 'salida'
    return 'ajuste'


def require_variant_if_product_has_variants(
    product: Product | int,
    variant: ProductVariant | None,
    *,
    context: str = 'movimiento de stock',
) -> None:
    """Productos con variantes activas deben registrar siempre talla/color."""
    product_id = product.id if isinstance(product, Product) else int(product)
    if product_has_variants(product_id) and variant is None:
        name = product.name if isinstance(product, Product) else f'producto #{product_id}'
        raise StockError(
            f'"{name}" tiene variantes: indique talla/color en el {context}.'
        )


@transaction.atomic
def apply_stock_delta(
    *,
    product: Product,
    almacen,
    delta: int,
    variant: ProductVariant | None = None,
    usuario=None,
    motivo: str,
    documento_ref: str = '',
    notas: str = '',
    costo_promedio=None,
    update_purchase_cost=None,
    create_kardex: bool = True,
    sync_variant_with_inventory: bool = True,
    inventory: Inventory | None = None,
) -> dict | None:
    """
    Aplica un cambio relativo (+/-) al stock.
    sync_variant_with_inventory=False para transferencias (solo mueve entre almacenes).
    """
    delta = int(delta)
    if delta == 0:
        return None

    if variant is not None and variant.product_id != product.id:
        raise StockError('La variante no pertenece al producto.')

    if create_kardex:
        require_variant_if_product_has_variants(product, variant)

    if inventory is not None:
        if inventory.product_id != product.id:
            raise StockError('El inventario no pertenece al producto.')
        inv = Inventory.objects.select_for_update().get(pk=inventory.pk)
        almacen = inv.almacen or almacen
    else:
        inv = get_or_create_inventory(product, almacen, lock=True)
    stock_anterior_inv = inv.quantity

    if update_purchase_cost is not None:
        inv.purchase_cost = update_purchase_cost

    if variant is not None and sync_variant_with_inventory:
        variant = ProductVariant.objects.select_for_update().get(pk=variant.pk)
        nuevo_var = int(variant.stock_extra or 0) + delta
        if nuevo_var < 0:
            raise StockError(
                f'Stock insuficiente en variante '
                f'({variant.talla} / {variant.color}). '
                f'Disponible: {variant.stock_extra}.'
            )
        variant.stock_extra = nuevo_var
        variant.save(update_fields=['stock_extra'])

    nuevo_inv = int(inv.quantity or 0) + delta
    if nuevo_inv < 0:
        raise StockError(
            f'Stock insuficiente en almacén para "{product.name}". '
            f'Disponible: {inv.quantity}.'
        )
    inv.quantity = nuevo_inv
    inv.save(update_fields=['quantity', 'purchase_cost'] if update_purchase_cost is not None else ['quantity'])

    movimiento = None
    if create_kardex:
        from vendors.models import KardexMovimiento

        movimiento = KardexMovimiento.objects.create(
            inventory=inv,
            almacen=almacen,
            variant=variant,
            tipo=kardex_tipo_from_delta(delta),
            motivo=motivo,
            cantidad=delta,
            stock_anterior=stock_anterior_inv,
            stock_actual=inv.quantity,
            costo_promedio=costo_promedio,
            documento_ref=documento_ref,
            usuario=usuario,
            notas=notas,
        )

    return {
        'inventory_id': inv.id,
        'stock_anterior': stock_anterior_inv,
        'stock_actual': inv.quantity,
        'variant_stock': int(variant.stock_extra) if variant else None,
        'movimiento_id': movimiento.id if movimiento else None,
    }


@transaction.atomic
def set_stock_absolute(
    *,
    product: Product,
    almacen,
    new_quantity: int,
    variant: ProductVariant | None = None,
    usuario=None,
    motivo: str = 'ajuste_manual',
    documento_ref: str = '',
    notas: str = '',
) -> dict | None:
    """Fija stock a un valor (conteo físico). Equivale a aplicar delta = nuevo - actual."""
    require_variant_if_product_has_variants(
        product, variant, context='conteo físico',
    )
    new_quantity = max(0, int(new_quantity))
    if variant is not None:
        current = get_system_stock(product.id, almacen.id, variant.id)
    else:
        current = get_system_stock(product.id, almacen.id)
    delta = new_quantity - current
    if delta == 0:
        return None
    return apply_stock_delta(
        product=product,
        almacen=almacen,
        delta=delta,
        variant=variant,
        usuario=usuario,
        motivo=motivo,
        documento_ref=documento_ref,
        notas=notas,
    )


def sum_variant_stock(product_id: int) -> int:
    agg = ProductVariant.objects.filter(
        product_id=product_id, is_active=True,
    ).aggregate(total=Sum('stock_extra'))
    return max(0, int(agg['total'] or 0))


def inventory_disponible(product_id: int, almacen_id=None) -> int:
    inv_qs = Inventory.objects.filter(product_id=product_id, is_active=True)
    if almacen_id is not None:
        inv_qs = inv_qs.filter(almacen_id=almacen_id)
    agg = inv_qs.aggregate(
        q=Sum('quantity'),
        r=Sum('reserved_quantity'),
    )
    q = int(agg['q'] or 0)
    r = int(agg['r'] or 0)
    return max(0, q - r)


def get_primary_inventory(product_id: int, almacen_id=None):
    """Inventario con más stock (o el del almacén indicado)."""
    qs = Inventory.objects.filter(product_id=product_id, is_active=True)
    if almacen_id is not None:
        return qs.filter(almacen_id=almacen_id).select_related('almacen', 'product').first()
    return qs.select_related('almacen', 'product').order_by('-quantity', 'id').first()


def get_precio_venta_lote(product_id: int, almacen_id=None) -> Decimal | None:
    """
    Retorna precio_venta del inventory más antiguo con stock > 0 (PEPS).
    Fallback: None (el caller usa Product.price).
    """
    qs = Inventory.objects.filter(
        product_id=product_id,
        is_active=True,
        quantity__gt=0,
    )
    if almacen_id:
        qs = qs.filter(almacen_id=almacen_id)
    inv = qs.order_by('created_at', 'id').first()
    return inv.precio_venta if inv else None


def check_available_for_sale(product_id: int, quantity: int, variant_id=None) -> None:
    """Valida stock vendible (variante o inventario agregado)."""
    qty = int(quantity)
    if variant_id:
        v = ProductVariant.objects.get(pk=variant_id, product_id=product_id, is_active=True)
        if v.stock_extra < qty:
            raise StockError(
                f'Stock insuficiente para variante ({v.talla} / {v.color}). '
                f'Disponible: {v.stock_extra}.'
            )
        return
    if product_has_variants(product_id):
        raise StockError('Este producto tiene variantes. Debe seleccionar talla/color.')
    inv = get_primary_inventory(product_id)
    if not inv or inv.available_quantity < qty:
        disp = inv.available_quantity if inv else 0
        raise StockError(f'Stock insuficiente. Disponible: {disp}.')


@transaction.atomic
def reserve_stock(product: Product, quantity: int, almacen=None) -> Inventory:
    """Reserva Live/web: solo incrementa reserved_quantity (no toca stock_extra ni quantity)."""
    qty = int(quantity)
    inv = get_primary_inventory(product.id, almacen.id if almacen else None)
    if not inv:
        inv = get_or_create_inventory(product, almacen, lock=True)
    else:
        inv = Inventory.objects.select_for_update().get(pk=inv.pk)
    if inv.available_quantity < qty:
        raise StockError(
            f'Stock insuficiente para reservar. Disponible: {inv.available_quantity}.'
        )
    inv.reserved_quantity = int(inv.reserved_quantity or 0) + qty
    inv.save(update_fields=['reserved_quantity'])
    return inv


@transaction.atomic
def release_reservation(product: Product, quantity: int, almacen=None) -> None:
    """Libera una reserva sin devolver venta."""
    qty = int(quantity)
    inv = get_primary_inventory(product.id, almacen.id if almacen else None)
    if not inv:
        return
    inv = Inventory.objects.select_for_update().get(pk=inv.pk)
    inv.reserved_quantity = max(0, int(inv.reserved_quantity or 0) - qty)
    inv.save(update_fields=['reserved_quantity'])


@transaction.atomic
def apply_inventory_kardex_delta(
    *,
    product: Product,
    almacen,
    delta: int,
    variant: ProductVariant | None = None,
    usuario=None,
    motivo: str,
    documento_ref: str = '',
    notas: str = '',
    costo_promedio=None,
    inventory: Inventory | None = None,
) -> dict | None:
    """Solo Inventory + Kardex (p. ej. lotes POS); no modifica stock_extra."""
    return apply_stock_delta(
        product=product,
        almacen=almacen,
        delta=delta,
        variant=variant,
        usuario=usuario,
        motivo=motivo,
        documento_ref=documento_ref,
        notas=notas,
        costo_promedio=costo_promedio,
        sync_variant_with_inventory=False,
        inventory=inventory,
    )


@transaction.atomic
def align_single_variant_stock_from_inventory(
    variant: ProductVariant,
    inventario_disponible: int,
    cantidad: int,
) -> None:
    """
    Una sola variante activa con inventario en sucursal pero stock_extra en 0:
    alinea el catálogo al físico antes de descontar en POS.
    """
    if cantidad <= 0 or inventario_disponible < cantidad:
        return
    n_active = ProductVariant.objects.filter(
        product_id=variant.product_id, is_active=True,
    ).count()
    if n_active != 1:
        return
    if int(variant.stock_extra or 0) >= cantidad:
        return
    ProductVariant.objects.filter(pk=variant.pk).update(
        stock_extra=max(0, int(inventario_disponible)),
    )
    variant.refresh_from_db()


def apply_variant_stock_delta(variant: ProductVariant, delta: int) -> int:
    """Solo stock_extra de variante (una vez por venta multi-lote)."""
    delta = int(delta)
    if delta == 0:
        return int(variant.stock_extra or 0)
    v = ProductVariant.objects.select_for_update().get(pk=variant.pk)
    nuevo = int(v.stock_extra or 0) + delta
    if nuevo < 0:
        raise StockError(
            f'Stock insuficiente en variante ({v.talla} / {v.color}). '
            f'Disponible: {v.stock_extra}.'
        )
    v.stock_extra = nuevo
    v.save(update_fields=['stock_extra'])
    return nuevo


@transaction.atomic
def fulfill_reservation_sale(
    *,
    product: Product,
    quantity: int,
    variant: ProductVariant | None,
    usuario,
    documento_ref: str,
    notas: str,
    almacen=None,
) -> None:
    """Entrega Live: descuenta stock real, libera reserva y registra kardex."""
    qty = int(quantity)
    inv = get_primary_inventory(product.id, almacen.id if almacen else None)
    if not inv:
        raise StockError('Sin inventario para confirmar la venta.')
    release_reservation(product, qty, inv.almacen)
    apply_stock_delta(
        product=product,
        almacen=inv.almacen,
        delta=-qty,
        variant=variant,
        usuario=usuario,
        motivo='venta_live',
        documento_ref=documento_ref,
        notas=notas,
    )


def reconcile_inventory_to_variants(product_id: int, almacen) -> int:
    """
    Alinea UN almacén con la suma de stock_extra (no replica el total en cada almacén).
  """
    if not product_has_variants(product_id):
        return get_system_stock(product_id, almacen.id if almacen else None)

    total_var = sum_variant_stock(product_id)
    product = Product.objects.get(pk=product_id)
    inv = get_or_create_inventory(product, almacen, lock=True)
    inv.quantity = total_var
    inv.save(update_fields=['quantity'])
    return total_var


def reconcile_product_variant_stock(product_id: int) -> int:
    """
    Ajusta el almacén con mayor stock para que quantity = suma(stock_extra).
    Los demás almacenes no se modifican (evita multiplicar stock).
    """
    if not product_has_variants(product_id):
        return 0
    inv = (
        Inventory.objects.filter(product_id=product_id, is_active=True)
        .select_related('almacen', 'product')
        .order_by('-quantity', 'id')
        .first()
    )
    if not inv:
        return 0
    return reconcile_inventory_to_variants(product_id, inv.almacen)


def rebuild_variant_stock_from_kardex(product_id: int) -> dict[int, int]:
    """
    Recalcula stock_extra de cada variante activa según la suma de cantidades en kardex.
    Útil cuando el catálogo quedó en 0 pero el almacén/kardex reflejan compras y ventas.
    """
    if not product_has_variants(product_id):
        return {}

    from vendors.models import KardexMovimiento

    updated: dict[int, int] = {}
    for v in ProductVariant.objects.filter(product_id=product_id, is_active=True):
        agg = KardexMovimiento.objects.filter(variant_id=v.id).aggregate(
            total=Sum('cantidad'),
        )
        nuevo = max(0, int(agg['total'] or 0))
        if int(v.stock_extra or 0) != nuevo:
            v.stock_extra = nuevo
            v.save(update_fields=['stock_extra'])
        updated[v.id] = nuevo
    return updated
