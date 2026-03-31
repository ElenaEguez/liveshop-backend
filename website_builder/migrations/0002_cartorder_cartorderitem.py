from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('website_builder', '0001_initial'),
        ('products', '0006_product_sell_by'),
        ('vendors', '0012_add_diferencia_cierre_turno'),
    ]

    operations = [
        migrations.CreateModel(
            name='CartOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_name', models.CharField(max_length=200)),
                ('customer_phone', models.CharField(max_length=20)),
                ('customer_email', models.CharField(blank=True, max_length=254)),
                ('customer_address', models.TextField(blank=True)),
                ('delivery_method', models.CharField(
                    choices=[('pickup', 'Recoger en tienda'), ('delivery', 'Envío a domicilio')],
                    max_length=20,
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pendiente'),
                        ('pending_confirmation', 'Pendiente de confirmación'),
                        ('confirmed', 'Confirmado'),
                        ('cancelled', 'Cancelado'),
                        ('delivered', 'Entregado'),
                    ],
                    default='pending',
                    max_length=25,
                )),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('payment_method', models.CharField(
                    choices=[
                        ('tigo_money', 'Tigo Money'),
                        ('banco_union', 'Banco Unión'),
                        ('efectivo', 'Efectivo'),
                    ],
                    max_length=20,
                )),
                ('payment_receipt', models.ImageField(blank=True, null=True, upload_to='website/receipts/')),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('vendor', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cart_orders',
                    to='vendors.vendor',
                )),
            ],
            options={
                'verbose_name': 'Pedido',
                'verbose_name_plural': 'Pedidos',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='CartOrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('variant_id', models.IntegerField(blank=True, null=True)),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=10)),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='items',
                    to='website_builder.cartorder',
                )),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='products.product',
                )),
            ],
            options={
                'verbose_name': 'Ítem de Pedido',
                'verbose_name_plural': 'Ítems de Pedido',
            },
        ),
    ]
