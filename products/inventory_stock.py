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
    sin_asignar = max(0, inv_disponible - variant_sum)

    return {
        'disponible_total': variant_sum,
        'variantes': variantes,
        'sin_asignar_variante': sin_asignar,
        'inventario_disponible': inv_disponible,
        'stock_scope': scope,
    }


def enrich_inventory_row(row: dict, almacen_id=None) -> dict:
    pid = row.get('product') or row.get('product_id')
    if not pid:
        return row
    stock = variant_stock_breakdown(int(pid), almacen_id)
    row['available_quantity'] = stock['disponible_total']
    row['variantes'] = stock['variantes']
    row['sin_asignar_variante'] = stock['sin_asignar_variante']
    row['inventario_disponible'] = stock['inventario_disponible']
    row['stock_scope'] = stock.get('stock_scope', 'global')
    return row
