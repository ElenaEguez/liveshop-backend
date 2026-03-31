from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, ProductViewSet, InventoryViewSet

router = DefaultRouter()
router.register(r'categories', CategoryViewSet)
router.register(r'inventories', InventoryViewSet, basename='inventory')
router.register(r'', ProductViewSet, basename='product')  # ← sin prefijo 'products'; debe ir último

urlpatterns = [
    path('', include(router.urls)),
]