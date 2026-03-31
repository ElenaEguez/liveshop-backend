from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VendorProfileView, VendorListView, VendorDetailView, VendorDashboardView, TeamMemberViewSet, CustomRoleViewSet, PublicPromocionesView

app_name = 'vendors'

router = DefaultRouter()
router.register(r'team',  TeamMemberViewSet,  basename='team-member')
router.register(r'roles', CustomRoleViewSet,  basename='custom-role')

urlpatterns = [
    path('profile/', VendorProfileView.as_view(), name='profile'),
    path('dashboard/', VendorDashboardView.as_view(), name='dashboard'),
    path('', include(router.urls)),
    path('', VendorListView.as_view(), name='vendor-list'),
    path('<slug:slug>/', VendorDetailView.as_view(), name='vendor-detail'),
    path('public/<slug:vendor_slug>/promociones/', PublicPromocionesView.as_view(), name='public-promociones'),
]