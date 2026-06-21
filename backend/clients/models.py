from django.conf import settings
from django.db import models


class Client(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=50)
    country = models.CharField(max_length=100, blank=True, default="")
    iin = models.CharField("ИИН/БИН", max_length=20, blank=True, default="")
    bank = models.CharField("Банк", max_length=150, blank=True, default="")
    bank_account = models.CharField("Расчётный счёт", max_length=34, blank=True, default="")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="client_profile",
    )

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.name
