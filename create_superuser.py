import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from vendors.models import Vendor

User = get_user_model()
ADMIN_EMAIL = 'admin@liveshop.com'

# Emails que deben tener fila en Vendor para que la API (dashboard, POS, pedidos web)
# responda 200. El admin Django (staff/superuser) NO sustituye a Vendor en REST.
DEV_VENDOR_EMAILS = (
    'admin@liveshop.com',
    'elena@liveshop.com',
)

# Create superuser if not exists
if not User.objects.filter(email=ADMIN_EMAIL).exists():
    User.objects.create_superuser(
        email=ADMIN_EMAIL,
        password='admin',
        nombre='admin',
        apellido='Liveshop',
    )
    print(f"[OK] Superusuario creado: {ADMIN_EMAIL}")
else:
    print("[OK] Superusuario ya existe")

# Local dev: ensure Vendor for each listed account (by email, case-insensitive).
for email in DEV_VENDOR_EMAILS:
    user = User.objects.filter(email__iexact=email).first()
    if not user:
        print(f"[SKIP] No hay usuario con email {email} — créalo (registro o admin) y vuelve a ejecutar este script.")
        continue
    if not Vendor.objects.filter(user=user).exists():
        Vendor.objects.create(user=user, nombre_tienda=f"Tienda {user.nombre or email.split('@')[0]}")
        print(f"[OK] Perfil de vendedor creado para {user.email}")
    else:
        print(f"[OK] Perfil de vendedor ya existe: {user.email}")
