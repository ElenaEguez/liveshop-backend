from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .pos_views import GastoViewSet, CategoriaGastoViewSet

gasto_router = DefaultRouter()
gasto_router.register(r'', GastoViewSet, basename='gasto')

categoria_router = DefaultRouter()
categoria_router.register(r'', CategoriaGastoViewSet, basename='categoria-gasto')

urlpatterns = [
    path('categorias/', include(categoria_router.urls)),
    path('', include(gasto_router.urls)),
]
