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

    from django.db import transaction
    from products.models import Inventory
    from vendors.models import KardexMovimiento

    with transaction.atomic():
        for item in instance.items.select_related('producto', 'variante').all():
            almacen_destino = item.almacen
            if not almacen_destino:
                raise ValueError(
                    'Cada ítem de compra debe tener almacén destino antes de recibir la orden.'
                )

            # ── 1. Actualizar Inventory ──────────────────────────
            inv, _ = Inventory.objects.get_or_create(
                product=item.producto,
                almacen=almacen_destino,
                defaults={
                    'quantity': 0,
                }
            )
            stock_anterior = inv.quantity
            inv.quantity = inv.quantity + item.cantidad
            inv.purchase_cost = item.costo_unitario_total
            inv.save(update_fields=['quantity', 'purchase_cost'])

            # ── 2. KardexMovimiento ──────────────────────────────
            KardexMovimiento.objects.create(
                inventory=inv,
                almacen=almacen_destino,
                variant=item.variante,
                tipo='entrada',
                motivo='compra',
                cantidad=item.cantidad,
                stock_anterior=stock_anterior,
                stock_actual=inv.quantity,
                costo_promedio=item.costo_unitario_total,
                documento_ref=f'OC-{instance.numero}',
                usuario=instance.created_by,
                notas=f'Compra OC-{instance.numero}',
            )

            # ── 3. Stock en variante (si aplica) ─────────────────
            if item.variante:
                variante = item.variante
                # ProductVariant no tiene campo "stock"; usa stock_extra
                if hasattr(variante, 'stock_extra'):
                    variante.stock_extra = variante.stock_extra + item.cantidad
                    variante.save(update_fields=['stock_extra'])

            # ── 4. Precio de venta oficial desde Compras ─────────
            if item.precio_venta_sugerido and item.precio_venta_sugerido > 0:
                producto = item.producto
                producto.price = item.precio_venta_sugerido
                producto.save(update_fields=['price'])
