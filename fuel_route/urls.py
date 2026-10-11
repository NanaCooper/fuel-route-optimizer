from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from routing import views

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='route-map', permanent=False)),
    path('api/route/', views.RoutePlanView.as_view(), name='route-plan'),
    path('api/route/map/', views.route_map, name='route-map'),
    path('api/places/', views.PlaceSearchView.as_view(), name='place-search'),
    path('api/health/', views.HealthView.as_view(), name='health'),
    path('admin/', admin.site.urls),
]
