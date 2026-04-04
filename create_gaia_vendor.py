import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from vendors.models import Vendor

User = get_user_model()

# Create user for Gaia Bolivia if not exists
if not User.objects.filter(email='gaia@bolivia.com').exists():
    user = User.objects.create_user(
        email='gaia@bolivia.com',
        password='gaia123',
        nombre='Gaia',
        apellido='Bolivia'
    )
    print("✓ Usuario creado: gaia@bolivia.com")
else:
    user = User.objects.get(email='gaia@bolivia.com')
    print("✓ Usuario ya existe")

# Create vendor if not exists
if not Vendor.objects.filter(slug='gaia-bolivia').exists():
    vendor = Vendor.objects.create(
        user=user,
        nombre_tienda='Gaia Bolivia Beauty',
        slug='gaia-bolivia',
        descripcion='Productos de belleza natural boliviana. Cuidamos tu piel con el poder de la naturaleza.',
        whatsapp='59162066644',
        instagram_url='https://www.instagram.com/gaia.bolivia/',
        is_verified=True,
        accepted_payment_methods='QR, Transferencia, Efectivo',
        moneda='Bs.'
    )
    print("✓ Vendor creado: gaia-bolivia")
else:
    print("✓ Vendor ya existe")