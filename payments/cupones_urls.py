from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .pos_views import CuponViewSet, CuponValidarView, PublicCuponValidarView

router = DefaultRouter()
router.register(r'', CuponViewSet, basename='cupon')

urlpatterns = [
    path('validar/', CuponValidarView.as_view(), name='cupon-validar'),
    path('public/validar/', PublicCuponValidarView.as_view(), name='cupon-public-validar'),
    path('', include(router.urls)),
]
