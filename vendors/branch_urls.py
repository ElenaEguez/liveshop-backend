from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .branch_views import SucursalViewSet, AlmacenViewSet, TicketConfigView, ComprobanteViewSet

router = DefaultRouter()
router.register(r'sucursales',    SucursalViewSet,    basename='sucursal')
router.register(r'almacenes',     AlmacenViewSet,     basename='almacen')
router.register(r'comprobantes',  ComprobanteViewSet, basename='comprobante')

urlpatterns = [
    path('ticket-config/', TicketConfigView.as_view(), name='ticket-config'),
    path('', include(router.urls)),
]
