from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0008_add_precio_venta_sugerido_compra'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ordencompra',
            name='numero',
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name='ordencompra',
            constraint=models.UniqueConstraint(
                fields=('vendor', 'numero'),
                name='compras_ordencompra_vendor_numero_uniq',
            ),
        ),
    ]
