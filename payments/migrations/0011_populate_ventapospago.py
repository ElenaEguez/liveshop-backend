from django.db import migrations


def populate_venta_pos_pagos(apps, schema_editor):
    VentaPOS = apps.get_model('payments', 'VentaPOS')
    VentaPOSPago = apps.get_model('payments', 'VentaPOSPago')

    batch = []
    for venta in VentaPOS.objects.filter(
        metodo_pago_id__isnull=False,
    ).only('id', 'metodo_pago_id', 'total').iterator(chunk_size=500):
        batch.append(
            VentaPOSPago(
                venta_id=venta.id,
                metodo_pago_id=venta.metodo_pago_id,
                monto=venta.total,
                orden=0,
            )
        )
        if len(batch) >= 500:
            VentaPOSPago.objects.bulk_create(batch)
            batch = []
    if batch:
        VentaPOSPago.objects.bulk_create(batch)


def unpopulate_venta_pos_pagos(apps, schema_editor):
    VentaPOSPago = apps.get_model('payments', 'VentaPOSPago')
    VentaPOSPago.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0010_add_ventapospago'),
    ]

    operations = [
        migrations.RunPython(
            populate_venta_pos_pagos,
            unpopulate_venta_pos_pagos,
        ),
    ]
