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
    Devuelve disponible_total, lista de variantes y unidades sin asignar.

    - Sin variantes: disponible_total = inventario (qty - reservado) en el alcance.
    - Con variantes y almacén: disponible_total = inventario físico de ese almacén
      (alineado con módulo Almacén); variantes muestran stock global de catálogo.
    - Con variantes sin almacén: total físico en todos los almacenes + desglose global.
    """
    inv_disponible = inventory_disponible(product_id, almacen_id)
    has_var = product_has_variants(product_id)

    if almacen_id is not None:
        variantes = _variant_list_global(product_id) if has_var else []
        return {
            'disponible_total': inv_disponible,
            'variantes': variantes,
            'sin_asignar_variante': 0,
            'inventario_disponible': inv_disponible,
            'stock_scope': 'almacen',
        }

    variantes = []
    variant_sum = 0
    if has_var:
        variantes = _variant_list_global(product_id)
        variant_sum = sum(v['disponible'] for v in variantes)
        disponible_total = inv_disponible
        sin_asignar = max(0, inv_disponible - variant_sum)
    else:
        disponible_total = inv_disponible
        sin_asignar = 0

    return {
        'disponible_total': disponible_total,
        'variantes': variantes,
        'sin_asignar_variante': sin_asignar,
        'inventario_disponible': inv_disponible,
        'stock_scope': 'global',
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
