from django.urls import path
from .views import (
    PublicStoreView,
    PublicCatalogView,
    PublicProductDetailView,
    PublicCategoriesView,
    PublicCheckoutView,
    PublicOrderStatusView,
    PublicReceiptUploadView,
    PublicOrderCancelView,
    VendorCartOrderListView,
    VendorCartOrderDetailView,
    VendorCartOrderConfirmView,
    VendorCartOrderCancelView,
    VendorCartOrderMarkDeliveredView,
    VendorCartOrderPendingCountView,
    VendorCartOrderDeleteView,
)

urlpatterns = [
    # ── Catálogo público ───────────────────────────────────────────────────
    path('public/<slug:vendor_slug>/', PublicStoreView.as_view(), name='public-store'),
    path('public/<slug:vendor_slug>/products/', PublicCatalogView.as_view(), name='public-catalog'),
    path('public/<slug:vendor_slug>/products/<int:pk>/', PublicProductDetailView.as_view(), name='public-product-detail'),
    path('public/<slug:vendor_slug>/categories/', PublicCategoriesView.as_view(), name='public-categories'),
    # ── Pedidos de ecommerce para el vendedor ───────────────────────────────
    path('orders/', VendorCartOrderListView.as_view(), name='vendor-cartorder-list'),
    path('orders/pending-count/', VendorCartOrderPendingCountView.as_view(), name='vendor-cartorder-pending-count'),
    path('orders/<int:pk>/', VendorCartOrderDetailView.as_view(), name='vendor-cartorder-detail'),
    path('orders/<int:pk>/confirm/', VendorCartOrderConfirmView.as_view(), name='vendor-cartorder-confirm'),
    path('orders/<int:pk>/cancel/', VendorCartOrderCancelView.as_view(), name='vendor-cartorder-cancel'),
    path('orders/<int:pk>/mark-delivered/', VendorCartOrderMarkDeliveredView.as_view(), name='vendor-cartorder-mark-delivered'),
    path('orders/<int:pk>/delete/', VendorCartOrderDeleteView.as_view(), name='vendor-cartorder-delete'),
    # ── Flujo de compra ────────────────────────────────────────────────────
    path('public/<slug:vendor_slug>/checkout/', PublicCheckoutView.as_view(), name='public-checkout'),
    path('public/<slug:vendor_slug>/order/<int:pk>/', PublicOrderStatusView.as_view(), name='public-order-status'),
    path('public/<slug:vendor_slug>/order/<int:pk>/receipt/', PublicReceiptUploadView.as_view(), name='public-receipt-upload'),
    path('public/<slug:vendor_slug>/order/<int:pk>/cancel/', PublicOrderCancelView.as_view(), name='public-order-cancel'),
]
