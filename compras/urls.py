from django.urls import path
from rest_framework.routers import DefaultRouter

from compras.views import (
    ProveedorViewSet,
    OrdenCompraViewSet,
    DevolucionCompraViewSet,
    BuscarDevolucionView,
)

router = DefaultRouter()
router.register('proveedores', ProveedorViewSet, basename='proveedor')
router.register('ordenes', OrdenCompraViewSet, basename='orden-compra')
router.register(
    'devoluciones-proveedor',
    DevolucionCompraViewSet,
    basename='devolucion-compra',
)

urlpatterns = [
    path(
        'buscar-devolucion/',
        BuscarDevolucionView.as_view(),
        name='buscar-devolucion',
    ),
] + router.urls
