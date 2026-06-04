"""Cálculo de stock disponible por producto y variante (alineado con reglas de stock_service)."""
from django.db.models import Sum

from .models import Inventory, ProductVariant
from .stock_service import inventory_disponible, product_has_variants, sum_variant_stock


def _variant_list_global(product_id: int):
    variantes = []
    for v in ProductVariant.objects.filter(
        product_id=product_id, is_active=True,
    ).order_by('talla', 'color', 'id'):
        d = max(0, int(v.stock_extra or 0))
        variantes.append({
            'id': v.id,
            'talla': v.talla or '',
            'color': v.color or '',
            'color_hex': v.color_hex or '',
            'sku': v.sku or '',
            'disponible': d,
        })
    return variantes


def _alm_ids_sucursal(sucursal_id: int | None) -> list[int]:
    if sucursal_id is None:
        return []
    from vendors.models import Almacen

    return list(
        Almacen.objects.filter(sucursal_id=sucursal_id, activo=True).values_list('id', flat=True)
    )


def _aggregate_disponible(inv_qs) -> int:
    agg = inv_qs.aggregate(q=Sum('quantity'), r=Sum('reserved_quantity'))
    q = int(agg['q'] or 0)
    r = int(agg['r'] or 0)
    return max(0, q - r)


def _has_assigned_inventory_sucursal(product_id: int, alm_ids: list[int]) -> bool:
    if not alm_ids:
        return False
    return Inventory.objects.filter(
        product_id=product_id,
        is_active=True,
        almacen_id__in=alm_ids,
    ).exists()


def _scale_variants_to_cap(variantes: list[dict], cap: int) -> tuple[list[dict], int]:
    """Reduce disponible por variante si la suma supera el inventario físico en sucursal."""
    total = sum(v['disponible'] for v in variantes)
    if total <= cap or total <= 0:
        return variantes, total

    remaining = cap
    scaled = []
    for i, v in enumerate(variantes):
        if i == len(variantes) - 1:
            d = remaining
        else:
            d = int(v['disponible'] * cap / total)
            remaining -= d
        scaled.append({**v, 'disponible': max(0, d)})
    new_total = sum(x['disponible'] for x in scaled)
    return scaled, new_total


def inventory_disponible_sucursal(product_id: int, sucursal_id: int | None) -> int:
    """Inventario disponible (qty - reservado) en almacenes activos de la sucursal."""
    if sucursal_id is None:
        return inventory_disponible(product_id, None)

    alm_ids = _alm_ids_sucursal(sucursal_id)
    disponible = 0
    if alm_ids:
        inv_qs = Inventory.objects.filter(
            product_id=product_id, is_active=True, almacen_id__in=alm_ids,
        )
        disponible = _aggregate_disponible(inv_qs)

    # Legacy pre-compras: inventario sin almacén solo si no hay filas asignadas a la sucursal
    if disponible == 0 and not _has_assigned_inventory_sucursal(product_id, alm_ids):
        legacy_qs = Inventory.objects.filter(
            product_id=product_id, is_active=True, almacen__isnull=True,
        )
        disponible = _aggregate_disponible(legacy_qs)

    return disponible


def filter_inventories_sucursal_lotes(qs, product_id: int, sucursal_id: int | None):
    """
    Restringe lotes de inventario vendibles en POS a la sucursal.
    Si no hay lotes en almacenes de la sucursal, usa filas legacy sin almacén.
    """
    qs = qs.filter(product_id=product_id)
    alm_ids = _alm_ids_sucursal(sucursal_id)
    if not alm_ids:
        return qs
    assigned = qs.filter(almacen_id__in=alm_ids)
    if assigned.exists():
        return assigned
    if not _has_assigned_inventory_sucursal(product_id, alm_ids):
        return qs.filter(almacen__isnull=True)
    return assigned


def variant_stock_breakdown_sucursal(product_id: int, sucursal_id: int | None = None):
    """
    Stock POS alineado con venta: inventario en almacenes de la sucursal (o legacy sin ubicación).
    Con variantes, el vendible es min(suma stock_extra, inventario físico en sucursal).
    """
    if sucursal_id is None:
        return variant_stock_breakdown(product_id, None)

    inv_disponible = inventory_disponible_sucursal(product_id, sucursal_id)
    has_var = product_has_variants(product_id)

    if not has_var:
        return {
            'disponible_total': inv_disponible,
            'variantes': [],
            'sin_asignar_variante': 0,
            'inventario_disponible': inv_disponible,
            'stock_scope': 'sucursal',
        }

    variantes = _variant_list_global(product_id)
    variant_sum = sum(v['disponible'] for v in variantes)

    if inv_disponible <= 0:
        for v in variantes:
            v['disponible'] = 0
        variant_sum = 0
    elif variant_sum <= 0 and len(variantes) == 1:
        # Una sola variante: el físico en sucursal es vendible aunque stock_extra esté en 0
        variantes[0]['disponible'] = inv_disponible
        variant_sum = inv_disponible
    elif variant_sum > 0:
        variantes, variant_sum = _scale_variants_to_cap(variantes, inv_disponible)

    return {
        'disponible_total': variant_sum,
        'variantes': variantes,
        'sin_asignar_variante': max(0, inv_disponible - variant_sum),
        'inventario_disponible': inv_disponible,
        'stock_scope': 'sucursal',
    }


def variant_stock_breakdown(product_id: int, almacen_id=None):
    """
    Devuelve disponible vendible, lista de variantes y unidades físicas sin asignar al catálogo.

    - Sin variantes: disponible_total = inventario (qty - reservado) en el alcance.
    - Con variantes: disponible_total = suma stock_extra (vendible en POS);
      inventario_disponible = físico en almacén(es); sin_asignar = físico − catálogo (no vendible).
    """
    inv_disponible = inventory_disponible(product_id, almacen_id)
    has_var = product_has_variants(product_id)
    scope = 'almacen' if almacen_id is not None else 'global'

    if not has_var:
        return {
            'disponible_total': inv_disponible,
            'variantes': [],
            'sin_asignar_variante': 0,
            'inventario_disponible': inv_disponible,
            'stock_scope': scope,
        }

    variantes = _variant_list_global(product_id)
    variant_sum = sum(v['disponible'] for v in variantes)
    if variant_sum > 0 and inv_disponible > 0 and almacen_id is not None:
        variantes, variant_sum = _scale_variants_to_cap(variantes, inv_disponible)
    sin_asignar = max(0, inv_disponible - variant_sum)

    return {
        'disponible_total': variant_sum,
        'variantes': variantes,
        'sin_asignar_variante': sin_asignar,
        'inventario_disponible': inv_disponible,
        'stock_scope': scope,
    }


def enrich_inventory_row(row: dict, almacen_id=None, sucursal_id=None) -> dict:
    pid = row.get('product') or row.get('product_id')
    if not pid:
        return row
    if sucursal_id is not None and almacen_id is None:
        stock = variant_stock_breakdown_sucursal(int(pid), sucursal_id)
    else:
        stock = variant_stock_breakdown(int(pid), almacen_id)
    row['available_quantity'] = stock['disponible_total']
    row['variantes'] = stock['variantes']
    row['sin_asignar_variante'] = stock['sin_asignar_variante']
    row['inventario_disponible'] = stock['inventario_disponible']
    row['stock_scope'] = stock.get('stock_scope', 'global')
    return row
