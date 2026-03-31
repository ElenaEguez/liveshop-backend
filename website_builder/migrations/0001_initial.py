from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('vendors', '0012_add_diferencia_cierre_turno'),
    ]

    operations = [
        migrations.CreateModel(
            name='VendorWebsite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_published', models.BooleanField(default=False)),
                ('theme', models.CharField(
                    choices=[('default', 'Default'), ('minimal', 'Minimal'), ('bold', 'Bold')],
                    default='default', max_length=50
                )),
                ('custom_css', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('vendor', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='website',
                    to='vendors.vendor',
                )),
            ],
            options={'verbose_name': 'Sitio Web', 'verbose_name_plural': 'Sitios Web'},
        ),
        migrations.CreateModel(
            name='WebsitePage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('slug', models.SlugField()),
                ('content', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('order', models.IntegerField(default=0)),
                ('website', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pages',
                    to='website_builder.vendorwebsite',
                )),
            ],
            options={
                'verbose_name': 'Página',
                'verbose_name_plural': 'Páginas',
                'ordering': ['order'],
                'unique_together': {('website', 'slug')},
            },
        ),
        migrations.CreateModel(
            name='WebsiteBanner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='website/banners/')),
                ('title', models.CharField(blank=True, max_length=200)),
                ('link', models.URLField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('order', models.IntegerField(default=0)),
                ('website', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='banners',
                    to='website_builder.vendorwebsite',
                )),
            ],
            options={
                'verbose_name': 'Banner',
                'verbose_name_plural': 'Banners',
                'ordering': ['order'],
            },
        ),
    ]
