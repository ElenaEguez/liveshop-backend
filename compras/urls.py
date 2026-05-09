from rest_framework.routers import DefaultRouter
from compras.views import ProveedorViewSet, OrdenCompraViewSet

router = DefaultRouter()
router.register('proveedores', ProveedorViewSet, basename='proveedor')
router.register('ordenes', OrdenCompraViewSet, basename='orden-compra')

urlpatterns = router.urls
from rest_framework.routers import DefaultRouter
from compras.views import ProveedorViewSet, OrdenCompraViewSet

router = DefaultRouter()
router.register('proveedores', ProveedorViewSet, basename='proveedor')
router.register('ordenes', OrdenCompraViewSet, basename='orden-compra')

urlpatterns = router.urls
