from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0006_product_sell_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='shipping_cost',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10,
                null=True, verbose_name='Costo de flete/importación'
            ),
        ),
    ]
