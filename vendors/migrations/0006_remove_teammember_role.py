from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0005_customrole_granular_perms'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='teammember',
            name='role',
        ),
    ]
