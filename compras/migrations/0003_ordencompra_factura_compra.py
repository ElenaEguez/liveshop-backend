from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0002_add_costo_fields_almacen_to_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordencompra',
            name='factura_compra',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
    ]
