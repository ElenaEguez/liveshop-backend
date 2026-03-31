from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0003_teammember'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('perm_catalog', models.BooleanField(default=False, verbose_name='Productos / Inventario / Lives / Mi Tienda')),
                ('perm_orders', models.BooleanField(default=True, verbose_name='Pedidos')),
                ('perm_payments', models.BooleanField(default=False, verbose_name='Pagos')),
                ('perm_team', models.BooleanField(default=False, verbose_name='Equipo')),
                ('perm_dashboard', models.BooleanField(default=False, verbose_name='Dashboard')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('vendor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_roles', to='vendors.vendor')),
            ],
            options={
                'verbose_name': 'Rol personalizado',
                'verbose_name_plural': 'Roles personalizados',
                'unique_together': {('vendor', 'name')},
            },
        ),
        migrations.AddField(
            model_name='teammember',
            name='custom_role',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='members',
                to='vendors.customrole',
                verbose_name='Rol personalizado',
            ),
        ),
        migrations.AlterField(
            model_name='teammember',
            name='role',
            field=models.CharField(
                max_length=20,
                choices=[('assistant', 'Asistente de panel'), ('payments', 'Encargado de pagos'), ('admin', 'Administrador')],
                default='assistant',
                blank=True,
            ),
        ),
    ]
