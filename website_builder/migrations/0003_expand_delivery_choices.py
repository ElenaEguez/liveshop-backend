from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website_builder', '0002_cartorder_cartorderitem'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cartorder',
            name='delivery_method',
            field=models.CharField(
                choices=[
                    ('pickup', 'Recoger en tienda'),
                    ('delivery', 'Envío a domicilio'),
                    ('envio_nacional', 'Envío nacional'),
                    ('envio_scz', 'Envío Departamento SCZ'),
                ],
                max_length=20,
            ),
        ),
    ]
