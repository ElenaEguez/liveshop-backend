from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet,
    InventoryViewSet,
    ProductViewSet,
    PublicCategoryListView,
    InventoryAdjustView,
    VariantStockAdjustView,
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'inventories', InventoryViewSet, basename='inventory')
router.register(r'', ProductViewSet, basename='product')  # ← sin prefijo 'products'; debe ir último

urlpatterns = [
    path('inventory/<int:pk>/adjust/', InventoryAdjustView.as_view(), name='inventory-adjust'),
    path('variants/<int:pk>/adjust-stock/', VariantStockAdjustView.as_view(), name='variant-stock-adjust'),
    path('public/<slug:vendor_slug>/categories/', PublicCategoryListView.as_view(), name='public-category-list'),
    path('', include(router.urls)),
]