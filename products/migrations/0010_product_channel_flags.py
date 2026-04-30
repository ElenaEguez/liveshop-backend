from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0009_add_category_parent_and_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_active_live',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='is_active_pos',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='is_active_web',
            field=models.BooleanField(default=True),
        ),
    ]
