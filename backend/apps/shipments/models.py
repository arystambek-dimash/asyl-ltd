from django.db import models


class Shipment(models.Model):
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="shipment"
    )
    # Поезд едет без номера машины — поле необязательно.
    truck_number = models.CharField(max_length=30, blank=True, default="")
    # Вес КАМАЗа при прибытии (на въезде). У поезда взвешивания нет.
    weigh_in_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bags_loaded = models.PositiveIntegerField(default=0)
    arrived_at = models.DateTimeField(null=True, blank=True)
    loading_started_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
