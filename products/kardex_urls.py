from django.urls import path
from .kardex_views import KardexListView, KardexAjusteView

urlpatterns = [
    path('kardex/ajuste/', KardexAjusteView.as_view(), name='kardex-ajuste'),
    path('kardex/', KardexListView.as_view(), name='kardex-list'),
]
