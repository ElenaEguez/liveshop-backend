from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PaymentViewSet,
    PublicPaymentCreateView,
    DevolucionViewSet,
    BuscarVentaParaDevolucionView,
)

router = DefaultRouter()
router.register(r'payments', PaymentViewSet)
router.register(r'devoluciones', DevolucionViewSet, basename='devolucion')

urlpatterns = [
    path(
        'devoluciones/buscar-venta/',
        BuscarVentaParaDevolucionView.as_view(),
        name='buscar-venta-devolucion'
    ),
    path('', include(router.urls)),
    path('public/submit/', PublicPaymentCreateView.as_view(), name='public-payment-create'),
]
