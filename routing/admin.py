from django.contrib import admin

from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ('opis_id', 'name', 'city', 'state', 'price', 'geo_source')
    list_filter = ('state', 'geo_source')
    search_fields = ('name', 'city', 'opis_id')
