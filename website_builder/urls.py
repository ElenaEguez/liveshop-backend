from django.urls import path
from .views import (
    PublicStoreView,
    PublicCatalogView,
    PublicProductDetailView,
    PublicCategoriesView,
    PublicCheckoutView,
    PublicOrderStatusView,
    PublicReceiptUploadView,
)

urlpatterns = [
    # ── Catálogo público ───────────────────────────────────────────────────
    path('public/<slug:vendor_slug>/', PublicStoreView.as_view(), name='public-store'),
    path('public/<slug:vendor_slug>/products/', PublicCatalogView.as_view(), name='public-catalog'),
    path('public/<slug:vendor_slug>/products/<int:pk>/', PublicProductDetailView.as_view(), name='public-product-detail'),
    path('public/<slug:vendor_slug>/categories/', PublicCategoriesView.as_view(), name='public-categories'),
    # ── Flujo de compra ────────────────────────────────────────────────────
    path('public/<slug:vendor_slug>/checkout/', PublicCheckoutView.as_view(), name='public-checkout'),
    path('public/<slug:vendor_slug>/order/<int:pk>/', PublicOrderStatusView.as_view(), name='public-order-status'),
    path('public/<slug:vendor_slug>/order/<int:pk>/receipt/', PublicReceiptUploadView.as_view(), name='public-receipt-upload'),
]
