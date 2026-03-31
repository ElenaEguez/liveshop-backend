from django.urls import path
from .views import RegisterView, LoginView, RefreshView, MeView

app_name = 'users'

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/refresh/', RefreshView.as_view(), name='token_refresh'),
    path('auth/me/', MeView.as_view(), name='me'),
]
