"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from livestreams.views import live_activo_redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    # ── Permanent public links (vendor-facing, outside /api/) ─────────────
    path('tienda/<slug:vendor_slug>/live-ahora/', live_activo_redirect, {'slot': 1}, name='live_activo_slot_default'),
    path('tienda/<slug:vendor_slug>/live-ahora/<int:slot>/', live_activo_redirect, name='live_activo_redirect'),
    # ── Public ecommerce endpoints (no auth required) ──────────────────────
    path('api/', include('website_builder.urls')),
    path('api/website-builder/', include('website_builder.urls')),
    path('api/v1/', include([
        # ── existing apps ─────────────────────────────────────────────────
        path('', include('users.urls')),
        path('vendors/', include('vendors.urls')),
        path('products/', include('products.urls')),
        path('livestreams/', include('livestreams.urls')),
        path('orders/', include('orders.urls')),
        path('payments/', include('payments.urls')),
        # ── POS / branches ────────────────────────────────────────────────
        path('branches/', include('vendors.branch_urls')),
        path('inventory/', include('products.kardex_urls')),
        path('pos/', include('payments.pos_urls')),
        path('gastos/', include('payments.gastos_urls')),
        path('cupones/', include('payments.cupones_urls')),
    ])),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
