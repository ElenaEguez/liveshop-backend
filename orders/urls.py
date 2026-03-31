from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ReservationViewSet, PublicReservationCreateView, OrdersDashboardView

router = DefaultRouter()
router.register(r'reservations', ReservationViewSet, basename='reservation')

urlpatterns = [
    path('dashboard/', OrdersDashboardView.as_view(), name='orders-dashboard'),
    path('', include(router.urls)),
    path(
        'public/live/<slug:slug>/reserve/',
        PublicReservationCreateView.as_view(),
        name='public-reservation-create'
    ),
]
