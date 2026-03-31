from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0004_customrole_update_teammember'),
    ]

    operations = [
        # Remove old combined field
        migrations.RemoveField(
            model_name='customrole',
            name='perm_catalog',
        ),
        # Add granular fields
        migrations.AddField(
            model_name='customrole',
            name='perm_products',
            field=models.BooleanField(default=False, verbose_name='Productos'),
        ),
        migrations.AddField(
            model_name='customrole',
            name='perm_categories',
            field=models.BooleanField(default=False, verbose_name='Categorías'),
        ),
        migrations.AddField(
            model_name='customrole',
            name='perm_inventory',
            field=models.BooleanField(default=False, verbose_name='Inventario'),
        ),
        migrations.AddField(
            model_name='customrole',
            name='perm_live_sessions',
            field=models.BooleanField(default=False, verbose_name='Lives'),
        ),
        migrations.AddField(
            model_name='customrole',
            name='perm_my_store',
            field=models.BooleanField(default=False, verbose_name='Mi Tienda'),
        ),
    ]
