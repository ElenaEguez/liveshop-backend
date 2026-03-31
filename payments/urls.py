from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentViewSet, PublicPaymentCreateView

router = DefaultRouter()
router.register(r'payments', PaymentViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('public/submit/', PublicPaymentCreateView.as_view(), name='public-payment-create'),
]
