from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LiveSessionViewSet, PublicLiveSessionDetailView

router = DefaultRouter()
router.register(r'live-sessions', LiveSessionViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('public/<slug:slug>/', PublicLiveSessionDetailView.as_view(), name='public-live-detail'),
]
