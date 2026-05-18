# Generated manually for LiveShop devoluciones

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0007_add_devolucion_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='devolucion',
            name='metodo_pago_devolucion',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='devoluciones',
                to='payments.metodopago',
            ),
        ),
        migrations.AlterField(
            model_name='devolucion',
            name='tipo_resolucion',
            field=models.CharField(
                choices=[('devolucion_dinero', 'Devolución de dinero')],
                default='devolucion_dinero',
                max_length=20,
            ),
        ),
    ]
