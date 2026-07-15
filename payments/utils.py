"""
Helpers de arqueo POS (sin dependencia de pos_views para evitar imports circulares).
"""
from decimal import Decimal

from django.db.models import Sum, Count, Q

from vendors.models import MovimientoCaja

from .models import VentaPOS, VentaPOSPago, PagoCredito


_STATUSES_ENTRADA_V2 = ('completada', 'parcialmente_devuelto', 'devuelto')


def _to_decimal(val):
    """
    SQLite aggregates sometimes return float; mixing Decimal + float raises TypeError.
    """
    if val is None:
        return Decimal('0')
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal('0')


def _calc_efectivo_esperado_turno_v1(turno):
    """
    Fórmula histórica (arqueo_v2=False). Intacta — no modificar comportamiento.
    Incluye ventas al contado en efectivo (por línea VentaPOSPago),
    abonos de crédito en efectivo y créditos cobrados de una vez, sin doble contar.
    """
    ventas_contado_ef = VentaPOSPago.objects.filter(
        venta__turno=turno,
        venta__status='completada',
        venta__es_credito=False,
        metodo_pago__tipo='efectivo',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ventas_contado_ef_legacy = VentaPOS.objects.filter(
        turno=turno,
        status='completada',
        es_credito=False,
        metodo_pago__tipo='efectivo',
    ).annotate(n_pagos_pos=Count('pagos')).filter(
        n_pagos_pos=0,
    ).aggregate(t=Sum('total'))['t'] or Decimal('0')

    pagos_ef = PagoCredito.objects.filter(
        venta__turno=turno,
        metodo_pago__tipo='efectivo',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ventas_credito_ef = VentaPOSPago.objects.filter(
        venta__turno=turno,
        venta__status='completada',
        venta__es_credito=True,
        metodo_pago__tipo='efectivo',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ventas_credito_ef_legacy = VentaPOS.objects.filter(
        turno=turno,
        status='completada',
        es_credito=True,
        metodo_pago__tipo='efectivo',
    ).annotate(
        n_pagos_credito=Count('pagos_credito'),
        n_pagos_pos=Count('pagos'),
    ).filter(
        n_pagos_credito=0,
        n_pagos_pos=0,
    ).aggregate(t=Sum('total'))['t'] or Decimal('0')

    ingresos = MovimientoCaja.objects.filter(turno=turno, tipo='ingreso').aggregate(
        t=Sum('monto')
    )['t'] or Decimal('0')
    retiros = MovimientoCaja.objects.filter(turno=turno, tipo='retiro').aggregate(
        t=Sum('monto')
    )['t'] or Decimal('0')

    ventas_contado_ef = _to_decimal(ventas_contado_ef) + _to_decimal(ventas_contado_ef_legacy)
    pagos_ef = _to_decimal(pagos_ef)
    ventas_credito_ef = _to_decimal(ventas_credito_ef) + _to_decimal(ventas_credito_ef_legacy)
    total_efectivo_ventas = ventas_contado_ef + pagos_ef + ventas_credito_ef
    efectivo_esperado = (
        _to_decimal(turno.monto_apertura)
        + total_efectivo_ventas
        + _to_decimal(ingresos)
        - _to_decimal(retiros)
    )

    return {
        'efectivo_esperado': efectivo_esperado,
        'ventas_contado_efectivo': ventas_contado_ef,
        'pagos_credito_efectivo': pagos_ef,
        'creditos_cobrados_efectivo': ventas_credito_ef,
        'total_efectivo_ventas': total_efectivo_ventas,
        'total_ingresos': _to_decimal(ingresos),
        'total_retiros': _to_decimal(retiros),
    }


def _ids_ventas_anuladas_tardias(turno):
    """
    Anulaciones cuyo efectivo quedó en la caja de este turno:
    - anulada_at NULL (legacy), o
    - anulada_at posterior al cierre del turno (anulación tardía).
    Anulación inmediata (anulada_at dentro del turno / turno aún abierto): excluir.
    """
    qs = VentaPOS.objects.filter(
        turno=turno,
        status='anulada',
        es_credito=False,
    )
    if turno.fecha_cierre:
        qs = qs.filter(
            Q(anulada_at__isnull=True) | Q(anulada_at__gt=turno.fecha_cierre)
        )
    else:
        qs = qs.filter(anulada_at__isnull=True)
    return list(qs.values_list('id', flat=True))


def _calc_efectivo_esperado_turno_v2(turno):
    """
    Arqueo íntegro (arqueo_v2=True):
    - Cuenta el efectivo que ENTRÓ (completada / parcial/totalmente devuelto
      + anulaciones tardías donde el dinero quedó en la caja del turno).
    - Resta salidas físicas vía MovimientoCaja (devolucion / anulacion).
    - Crédito: VentaPOSPago de es_credito=True NO suma; solo PagoCredito.turno.
    """
    # Contado: dinero que entró (status informativo; devoluciones restan por movimiento).
    ventas_contado_ef = VentaPOSPago.objects.filter(
        venta__turno=turno,
        venta__status__in=_STATUSES_ENTRADA_V2,
        venta__es_credito=False,
        metodo_pago__tipo='efectivo',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ventas_contado_ef_legacy = VentaPOS.objects.filter(
        turno=turno,
        status__in=_STATUSES_ENTRADA_V2,
        es_credito=False,
        metodo_pago__tipo='efectivo',
    ).annotate(n_pagos_pos=Count('pagos')).filter(
        n_pagos_pos=0,
    ).aggregate(t=Sum('total'))['t'] or Decimal('0')

    anuladas_incluir_ids = _ids_ventas_anuladas_tardias(turno)
    ventas_anuladas_ef = Decimal('0')
    ventas_anuladas_ef_legacy = Decimal('0')
    if anuladas_incluir_ids:
        ventas_anuladas_ef = VentaPOSPago.objects.filter(
            venta_id__in=anuladas_incluir_ids,
            metodo_pago__tipo='efectivo',
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
        ventas_anuladas_ef_legacy = VentaPOS.objects.filter(
            id__in=anuladas_incluir_ids,
            metodo_pago__tipo='efectivo',
        ).annotate(n_pagos_pos=Count('pagos')).filter(
            n_pagos_pos=0,
        ).aggregate(t=Sum('total'))['t'] or Decimal('0')

    # Abonos de crédito atados al turno donde se cobraron (no al de la venta).
    pagos_ef = PagoCredito.objects.filter(
        turno=turno,
        metodo_pago__tipo='efectivo',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    # C2: no sumar VentaPOSPago de ventas a crédito (evita doble conteo).
    ventas_credito_ef = Decimal('0')

    ingresos = MovimientoCaja.objects.filter(turno=turno, tipo='ingreso').aggregate(
        t=Sum('monto')
    )['t'] or Decimal('0')
    retiros = MovimientoCaja.objects.filter(turno=turno, tipo='retiro').aggregate(
        t=Sum('monto')
    )['t'] or Decimal('0')
    salidas_devolucion = MovimientoCaja.objects.filter(
        turno=turno, tipo='devolucion',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
    salidas_anulacion = MovimientoCaja.objects.filter(
        turno=turno, tipo='anulacion',
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

    ventas_contado_ef = (
        _to_decimal(ventas_contado_ef)
        + _to_decimal(ventas_contado_ef_legacy)
        + _to_decimal(ventas_anuladas_ef)
        + _to_decimal(ventas_anuladas_ef_legacy)
    )
    pagos_ef = _to_decimal(pagos_ef)
    total_efectivo_ventas = ventas_contado_ef + pagos_ef + ventas_credito_ef
    efectivo_esperado = (
        _to_decimal(turno.monto_apertura)
        + total_efectivo_ventas
        + _to_decimal(ingresos)
        - _to_decimal(retiros)
        - _to_decimal(salidas_devolucion)
        - _to_decimal(salidas_anulacion)
    )

    return {
        'efectivo_esperado': efectivo_esperado,
        'ventas_contado_efectivo': ventas_contado_ef,
        'pagos_credito_efectivo': pagos_ef,
        'creditos_cobrados_efectivo': ventas_credito_ef,
        'total_efectivo_ventas': total_efectivo_ventas,
        'total_ingresos': _to_decimal(ingresos),
        'total_retiros': _to_decimal(retiros),
        'total_devoluciones_efectivo': _to_decimal(salidas_devolucion),
        'total_anulaciones_efectivo': _to_decimal(salidas_anulacion),
    }


def _calc_efectivo_esperado_turno(turno):
    """
    Efectivo que debería haber en caja al cerrar el turno.
    arqueo_v2=False → fórmula histórica intacta; True → arqueo íntegro.
    """
    if getattr(turno, 'arqueo_v2', False):
        return _calc_efectivo_esperado_turno_v2(turno)
    return _calc_efectivo_esperado_turno_v1(turno)


def _metodo_venta_arqueo(venta):
    """
    Retorna lista de (tipo, nombre, monto) por línea VentaPOSPago.
    Sin líneas de pago → fallback al método/total de la venta (compatibilidad).
    """
    pagos_pos = list(venta.pagos.all())
    if pagos_pos:
        lineas = []
        for p in pagos_pos:
            mp = p.metodo_pago
            if mp:
                lineas.append((
                    mp.tipo or 'otro',
                    mp.nombre,
                    _to_decimal(p.monto),
                ))
            else:
                lineas.append(('otro', 'Otro', _to_decimal(p.monto)))
        return lineas

    if venta.es_credito:
        return [('credito', 'Crédito', _to_decimal(venta.total))]
    if venta.metodo_pago:
        mp = venta.metodo_pago
        return [(mp.tipo or 'otro', mp.nombre, _to_decimal(venta.total))]
    return [('otro', 'Otro', _to_decimal(venta.total))]


def _aggregate_ventas_arqueo(ventas_qs):
    """Totales por cajero y por método (incluye Crédito) para arqueos."""
    cajero_map = {}
    global_metodo = {}
    for v in ventas_qs.select_related(
        'metodo_pago', 'usuario',
    ).prefetch_related('pagos__metodo_pago'):
        lineas = _metodo_venta_arqueo(v)
        venta_monto = sum(m for _, _, m in lineas)

        uid = v.usuario_id
        if uid is not None:
            if uid not in cajero_map:
                nom = (getattr(v.usuario, 'nombre', None) or '').strip()
                ape = (getattr(v.usuario, 'apellido', None) or '').strip()
                name = f'{nom} {ape}'.strip()
                if not name:
                    name = getattr(v.usuario, 'email', None) or '—'
                cajero_map[uid] = {
                    'id': uid, 'nombre': name,
                    'total': Decimal('0'), 'por_metodo': {},
                }
            cajero_map[uid]['total'] += venta_monto
            for tipo, nombre, monto in lineas:
                pm = cajero_map[uid]['por_metodo'].setdefault(
                    tipo, {'nombre': nombre, 'total': Decimal('0'), 'cantidad': 0}
                )
                pm['total'] += monto
                pm['cantidad'] += 1

        for tipo, nombre, monto in lineas:
            gm = global_metodo.setdefault(
                tipo, {'nombre': nombre, 'total': Decimal('0'), 'cantidad': 0}
            )
            gm['total'] += monto
            gm['cantidad'] += 1

    totales_por_cajero = [
        {
            'id': v['id'],
            'nombre': v['nombre'],
            'total': str(_to_decimal(v['total']).quantize(Decimal('0.01'))),
            'por_metodo': [
                {'tipo': k, 'nombre': m['nombre'],
                 'total': str(_to_decimal(m['total']).quantize(Decimal('0.01'))),
                 'cantidad': m['cantidad']}
                for k, m in v['por_metodo'].items()
            ],
        }
        for v in cajero_map.values()
    ]
    totales_por_metodo = sorted(
        [
            {
                'tipo': tipo,
                'nombre': m['nombre'],
                'total': str(_to_decimal(m['total']).quantize(Decimal('0.01'))),
                'cantidad': m['cantidad'],
            }
            for tipo, m in global_metodo.items()
        ],
        key=lambda x: Decimal(x['total']),
        reverse=True,
    )
    return totales_por_cajero, totales_por_metodo
