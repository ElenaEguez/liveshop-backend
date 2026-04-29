from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, InventoryViewSet, ProductViewSet, PublicCategoryListView

router = DefaultRouter()
router.register(r'categories', CategoryViewSet)
router.register(r'inventories', InventoryViewSet, basename='inventory')
router.register(r'', ProductViewSet, basename='product')  # ← sin prefijo 'products'; debe ir último

urlpatterns = [
    path('public/<slug:vendor_slug>/categories/', PublicCategoryListView.as_view(), name='public-category-list'),
    path('', include(router.urls)),
]