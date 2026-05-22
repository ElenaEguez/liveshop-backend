"""Utilidades EAN-13 para códigos de barras escaneables en POS."""
import random
from typing import Optional

from django.db.models import QuerySet


def ean13_check_digit(digits12: str) -> int:
    """Dígito de control EAN-13 para 12 dígitos."""
    total = sum(
        int(digits12[i]) * (1 if i % 2 == 0 else 3)
        for i in range(12)
    )
    return (10 - (total % 10)) % 10


def is_valid_ean13(value: Optional[str]) -> bool:
    """True si es exactamente 13 dígitos con dígito de control válido."""
    if not value:
        return False
    code = str(value).strip()
    if len(code) != 13 or not code.isdigit():
        return False
    return int(code[12]) == ean13_check_digit(code[:12])


def build_ean13_from12(digits12: str) -> str:
    return digits12 + str(ean13_check_digit(digits12))


def generate_ean13_candidate() -> str:
    """12 dígitos aleatorios + dígito de control (prefijo 2 = uso interno)."""
    prefix = '2'
    rest = ''.join(str(random.randint(0, 9)) for _ in range(11))
    return build_ean13_from12(prefix + rest)


def generate_unique_ean13(
    existing: QuerySet,
    *,
    max_attempts: int = 200,
) -> str:
    """Genera EAN-13 único respecto al queryset de productos (campo barcode)."""
    for _ in range(max_attempts):
        candidate = generate_ean13_candidate()
        if not existing.filter(barcode=candidate).exists():
            return candidate
    raise ValueError('No se pudo generar un código EAN-13 único.')


def normalize_barcode_value(
    value: Optional[str],
    *,
    existing_products: QuerySet,
) -> Optional[str]:
    """
    Devuelve EAN-13 válido y único.
    Si value ya es EAN-13 válido, se conserva.
    Si está vacío o no es EAN-13 escaneable, genera uno nuevo.
    """
    if is_valid_ean13(value):
        code = str(value).strip()
        if existing_products.filter(barcode=code).exists():
            return generate_unique_ean13(existing_products)
        return code

    return generate_unique_ean13(existing_products)
