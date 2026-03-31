import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

# Create superuser if not exists
if not User.objects.filter(email='admin@liveshop.com').exists():
    User.objects.create_superuser(
        email='admin@liveshop.com',
        password='admin123',
        nombre='Admin',
        apellido='LiveShop'
    )
    print("✓ Superusuario creado: admin@liveshop.com")
else:
    print("✓ Superusuario ya existe")
