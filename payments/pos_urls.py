from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .pos_views import (
    VentaPOSViewSet, TurnoCajaViewSet, ProductoPOSSearchView,
    MetodoPagoViewSet, MovimientosCajaView, POSScanView,
)

router = DefaultRouter()
router.register(r'ventas', VentaPOSViewSet, basename='venta-pos')
router.register(r'turnos', TurnoCajaViewSet, basename='turno-caja')
router.register(r'metodos-pago', MetodoPagoViewSet, basename='metodo-pago')

urlpatterns = [
    path('buscar-producto/', ProductoPOSSearchView.as_view(), name='pos-buscar-producto'),
    path('scan/', POSScanView.as_view(), name='pos-scan'),
    path('movimientos/', MovimientosCajaView.as_view(), name='pos-movimientos-caja'),
    path('', include(router.urls)),
]
