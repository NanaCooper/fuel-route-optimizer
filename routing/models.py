from django.db import models


class FuelStation(models.Model):
    """A truck stop from the provided price list, geocoded offline (see `build_datasets`)."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.CharField(max_length=32, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=5, help_text='Retail price, USD per gallon')
    latitude = models.FloatField()
    longitude = models.FloatField()
    geo_source = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ['opis_id']
        indexes = [models.Index(fields=['latitude', 'longitude'])]

    def __str__(self):
        return f'{self.name} ({self.city}, {self.state}) ${self.price}'
