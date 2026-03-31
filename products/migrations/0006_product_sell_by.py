from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0005_inventory_almacen_product_barcode_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='sell_by',
            field=models.JSONField(blank=True, default=list, help_text='Lista de unidades: unidad, qq, lbs, lts', verbose_name='Se vende por'),
        ),
    ]
