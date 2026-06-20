from django.conf import settings
from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=200)
    contact = models.CharField(max_length=200)
    country = models.CharField(max_length=100, blank=True, default="")
    requisites = models.TextField(blank=True, default="")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="client_profile",
    )

    def __str__(self):
        return self.name
