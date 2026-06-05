from django.db.models.signals import pre_save
from django.dispatch import receiver
from compras.models import OrdenCompra


@receiver(pre_save, sender=OrdenCompra)
def procesar_recepcion_compra(sender, instance, **kwargs):
    """
    Cuando una OrdenCompra pasa a estado 'recibida':
    delega en _procesar_recepcion_inventario (compras.views).
    Si confirmar() ya aplicó inventario, no reprocesar.
    """
    if not instance.pk:
        return
    if getattr(instance, '_inventario_recepcion_aplicado', False):
        return

    try:
        anterior = OrdenCompra.objects.get(pk=instance.pk)
    except OrdenCompra.DoesNotExist:
        return

    if anterior.estado == instance.estado:
        return
    if instance.estado != 'recibida':
        return

    from compras.views import _procesar_recepcion_inventario
    from products.stock_service import StockError

    try:
        _procesar_recepcion_inventario(instance)
    except (ValueError, StockError):
        raise
    except Exception as exc:
        raise ValueError(str(exc)) from exc
