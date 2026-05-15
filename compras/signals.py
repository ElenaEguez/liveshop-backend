from django.db import transaction
from django.db.models.signals import pre_save
from django.dispatch import receiver
from compras.models import OrdenCompra


@receiver(pre_save, sender=OrdenCompra)
def procesar_recepcion_compra(sender, instance, **kwargs):
    """
    Cuando una OrdenCompra pasa a estado 'recibida':
    - Incrementa stock en Inventory por cada item
    - Crea KardexMovimiento de entrada
    - Si ProductVariant tiene stock, lo incrementa también
    """
    if not instance.pk:
        return  # objeto nuevo, nada que comparar

    try:
        anterior = OrdenCompra.objects.get(pk=instance.pk)
    except OrdenCompra.DoesNotExist:
        return

    # Solo actuar cuando el estado cambia A 'recibida'
    if anterior.estado == instance.estado:
        return
    if instance.estado != 'recibida':
        return

    from products.stock_service import (
        StockError,
        apply_stock_delta,
        product_has_variants,
    )

    try:
        with transaction.atomic():
            qs_items = instance.items.select_related(
                'producto', 'variante',
            ).prefetch_related('distribuciones__variante')
            for item in qs_items:
                almacen_destino = item.almacen or instance.almacen
                if not almacen_destino:
                    raise ValueError(
                        'Cada ítem de compra debe tener almacén destino antes de recibir la orden.'
                    )

                distribuciones = list(item.distribuciones.all())
                if distribuciones:
                    filas = [
                        (d.cantidad, d.variante)
                        for d in distribuciones
                        if d.cantidad and d.variante_id
                    ]
                elif item.variante_id:
                    filas = [(item.cantidad, item.variante)]
                elif product_has_variants(item.producto_id):
                    raise ValueError(
                        f'"{item.producto.name}": indique distribución por variante '
                        f'antes de recibir la compra.'
                    )
                else:
                    filas = [(item.cantidad, None)]

                for cant_u, variante in filas:
                    if not cant_u:
                        continue
                    apply_stock_delta(
                        product=item.producto,
                        almacen=almacen_destino,
                        delta=int(cant_u),
                        variant=variante,
                        usuario=instance.created_by,
                        motivo='compra',
                        documento_ref=f'OC-{instance.numero}',
                        notas=f'Compra OC-{instance.numero}',
                        costo_promedio=item.costo_unitario_total,
                        update_purchase_cost=item.costo_unitario_total,
                    )

                if item.precio_venta_sugerido and item.precio_venta_sugerido > 0:
                    producto = item.producto
                    producto.price = item.precio_venta_sugerido
                    producto.save(update_fields=['price'])
    except StockError as exc:
        raise ValueError(str(exc)) from exc
