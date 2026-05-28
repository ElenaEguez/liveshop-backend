"""
Mapeo de los 22 módulos de navegación → campos perm_* en CustomRole.
Conserva los 13 permisos originales; los 9 nuevos desglosan rutas agrupadas.
"""
from django.conf import settings

# Claves usadas en JWT (perms), MisPermisos (permisos) y layout (permission).
GRANULAR_MODULE_KEYS = [
    'dashboard',
    'expenses',
    'pos',
    'arqueos',
    'ventas_pos',
    'devoluciones',
    'conteos',
    'conteos_control',
    'transferencias',
    'almacen',
    'inventory',
    'compras',
    'proveedores',
    'products',
    'categories',
    'team',
    'configuracion',
    'my_store',
    'livestream',
    'pedidos',
    'pagos',
    'ecommerce_orders',
]

# routerLink → (clave módulo, campo CustomRole)
NAV_ROUTE_PERMISSION_MAP = [
    ('/dashboard', 'dashboard', 'perm_dashboard'),
    ('/gastos', 'expenses', 'perm_expenses'),
    ('/vendor/pos', 'pos', 'perm_pos'),
    ('/vendor/arqueos', 'arqueos', 'perm_arqueos'),
    ('/vendor/ventas', 'ventas_pos', 'perm_ventas_pos'),
    ('/devoluciones', 'devoluciones', 'perm_devoluciones'),
    ('/almacen/conteos', 'conteos', 'perm_conteos'),
    ('/almacen/conteos-control', 'conteos_control', 'perm_conteos_control'),
    ('/almacen/transferencias', 'transferencias', 'perm_transferencias'),
    ('/almacen', 'almacen', 'perm_almacen'),
    ('/inventory', 'inventory', 'perm_inventory'),
    ('/compras', 'compras', 'perm_compras'),
    ('/compras/proveedores', 'proveedores', 'perm_proveedores'),
    ('/products', 'products', 'perm_products'),
    ('/categories', 'categories', 'perm_categories'),
    ('/team', 'team', 'perm_team'),
    ('/configuracion', 'configuracion', 'perm_configuracion'),
    ('/my-store', 'my_store', 'perm_my_store'),
    ('/live-sessions', 'livestream', 'perm_live_sessions'),
    ('/orders', 'pedidos', 'perm_orders'),
    ('/payments', 'pagos', 'perm_payments'),
    ('/ecommerce-orders', 'ecommerce_orders', 'perm_ecommerce_orders'),
]

MODULE_TO_ROLE_FIELD = {mod: field for _, mod, field in NAV_ROUTE_PERMISSION_MAP}

# Claves heredadas (ModuloGuard / rutas antiguas) → OR de permisos granulares.
LEGACY_MODULE_ALIASES = {
    'reportes': ('dashboard', 'expenses', 'ecommerce_orders'),
    'productos': ('products', 'categories'),
    'configuracion': ('team', 'configuracion', 'my_store'),
    'inventario': ('inventory', 'conteos', 'conteos_control', 'transferencias', 'almacen'),
    'almacen': ('almacen', 'conteos_control', 'transferencias', 'conteos'),
}


# Módulos que heredan perm_warehouse (rol creado antes del desglose granular).
_WAREHOUSE_LEGACY_KEYS = frozenset({
    'almacen', 'transferencias', 'conteos', 'conteos_control', 'inventory',
})
# Módulos POS que heredan perm_pos.
_POS_LEGACY_KEYS = frozenset({'arqueos', 'ventas_pos', 'devoluciones'})
_LEGACY_GRANULAR_FALLBACK = bool(
    getattr(settings, 'PERMISSIONS_LEGACY_GRANULAR_FALLBACK', False)
)


def role_perm_value(role, module_key: str) -> bool:
    if role is None:
        return False
    field = MODULE_TO_ROLE_FIELD.get(module_key)
    if not field:
        return False
    val = bool(getattr(role, field, False))
    if not val and module_key == 'team':
        val = bool(getattr(role, 'perm_manage_roles', False))
    if _LEGACY_GRANULAR_FALLBACK and not val and module_key in _WAREHOUSE_LEGACY_KEYS:
        val = bool(getattr(role, 'perm_warehouse', False))
    if _LEGACY_GRANULAR_FALLBACK and not val and module_key in _POS_LEGACY_KEYS:
        val = bool(getattr(role, 'perm_pos', False))
    if not val and module_key == 'proveedores':
        val = bool(getattr(role, 'perm_compras', False))
    if not val and module_key == 'configuracion':
        val = bool(getattr(role, 'perm_team', False))
    return val


def build_permisos_modulos(role) -> dict:
    """Dict módulo → bool para los 22 ítems + alias legacy."""
    out = {key: role_perm_value(role, key) for key in GRANULAR_MODULE_KEYS}
    for legacy, parts in LEGACY_MODULE_ALIASES.items():
        out[legacy] = any(out.get(p, False) for p in parts)
    return out


def build_jwt_perms_dict(role) -> dict:
    """Payload perms en JWT (claves granulares + legacy)."""
    mod = build_permisos_modulos(role)
    return {
        'products': mod['products'],
        'categories': mod['categories'],
        'inventory': mod['inventory'],
        'live_sessions': mod['livestream'],
        'my_store': mod['my_store'],
        'orders': mod['pedidos'],
        'payments': mod['pagos'],
        'pedidos': mod['pedidos'],
        'pagos': mod['pagos'],
        'team': mod['team'],
        'manage_roles': bool(getattr(role, 'perm_manage_roles', False)),
        'dashboard': mod['dashboard'],
        'pos': mod['pos'],
        'warehouse': (
            mod['almacen'] or mod['transferencias'] or mod['conteos_control']
            or mod['conteos'] or bool(getattr(role, 'perm_warehouse', False))
        ),
        'expenses': mod['expenses'],
        'compras': mod['compras'] or mod['proveedores'],
        'arqueos': mod['arqueos'],
        'ventas_pos': mod['ventas_pos'],
        'devoluciones': mod['devoluciones'],
        'conteos': mod['conteos'],
        'conteos_control': mod['conteos_control'],
        'transferencias': mod['transferencias'],
        'almacen': mod['almacen'],
        'proveedores': mod['proveedores'],
        'configuracion': mod['configuracion'],
        'ecommerce_orders': mod['ecommerce_orders'],
        'livestream': mod['livestream'],
    }


def all_modules_true() -> dict:
    return {key: True for key in GRANULAR_MODULE_KEYS}
