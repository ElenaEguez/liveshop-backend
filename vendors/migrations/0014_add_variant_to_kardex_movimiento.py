import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0013_add_inventory_method_and_created_at'),
        ('products', '0008_add_inventory_method_and_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='kardexmovimiento',
            name='variant',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='kardex_movimientos',
                to='products.productvariant',
                verbose_name='Variante',
            ),
        ),
    ]
