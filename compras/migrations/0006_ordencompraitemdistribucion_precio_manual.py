import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0005_devolucioncompra_orden_compra'),
        ('products', '0004_productvariant'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordencompraitem',
            name='precio_venta_es_manual',
            field=models.BooleanField(
                default=False,
                help_text='Si es True, no se recalcula precio_venta_sugerido desde el % de ganancia',
            ),
        ),
        migrations.CreateModel(
            name='OrdenCompraItemDistribucion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad', models.PositiveIntegerField(default=0)),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='distribuciones',
                    to='compras.ordencompraitem',
                )),
                ('variante', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='compra_distribuciones',
                    to='products.productvariant',
                )),
            ],
            options={
                'verbose_name': 'Distribución ítem OC',
                'verbose_name_plural': 'Distribuciones ítem OC',
                'unique_together': {('item', 'variante')},
            },
        ),
    ]
