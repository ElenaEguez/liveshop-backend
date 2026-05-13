# Generated manually for traceability of returns from purchase orders

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0004_devolucion_compra_and_kardex_motivo'),
    ]

    operations = [
        migrations.AddField(
            model_name='devolucioncompra',
            name='orden_compra',
            field=models.ForeignKey(
                blank=True,
                help_text='Si la devolución se originó desde una orden recibida',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='devoluciones',
                to='compras.ordencompra',
            ),
        ),
    ]
