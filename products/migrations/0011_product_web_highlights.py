from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0010_product_channel_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='web_is_bestseller',
            field=models.BooleanField(default=False, verbose_name='Destacar en más vendidos (web)'),
        ),
        migrations.AddField(
            model_name='product',
            name='web_is_new',
            field=models.BooleanField(default=False, verbose_name='Destacar en nuevos ingresos (web)'),
        ),
        migrations.AddField(
            model_name='product',
            name='compare_at_price',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Opcional. Si es mayor al precio actual, se muestra % de descuento en la tienda.',
                max_digits=10,
                null=True,
                verbose_name='Precio anterior (web)',
            ),
        ),
    ]
