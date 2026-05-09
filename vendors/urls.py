from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    VendorProfileView,
    VendorListView,
    VendorDetailView,
    VendorDashboardView,
    TeamMemberViewSet,
    CustomRoleViewSet,
    ConteoFisicoViewSet,
    TransferenciaAlmacenViewSet,
    PublicPromocionesView,
    MisPermisosView,
)
from .views_admin import (
    VendorAdminListView,
    VendorAdminDetailView,
    VendorToggleEstadoView,
)

app_name = 'vendors'

router = DefaultRouter()
router.register(r'team',  TeamMemberViewSet,  basename='team-member')
router.register(r'roles', CustomRoleViewSet,  basename='custom-role')
router.register(r'conteos', ConteoFisicoViewSet, basename='conteo-fisico')
router.register(r'transferencias', TransferenciaAlmacenViewSet,
                basename='transferencia')

urlpatterns = [
    path('profile/', VendorProfileView.as_view(), name='profile'),
    path('dashboard/', VendorDashboardView.as_view(), name='dashboard'),
    path('mis-permisos/', MisPermisosView.as_view(), name='mis-permisos'),
    path('admin/vendors/', VendorAdminListView.as_view()),
    path('admin/vendors/<int:pk>/', VendorAdminDetailView.as_view()),
    path('admin/vendors/<int:pk>/estado/', VendorToggleEstadoView.as_view()),
    path('', include(router.urls)),
    path('', VendorListView.as_view(), name='vendor-list'),
    path('<slug:slug>/', VendorDetailView.as_view(), name='vendor-detail'),
    path('public/<slug:vendor_slug>/promociones/', PublicPromocionesView.as_view(), name='public-promociones'),
]