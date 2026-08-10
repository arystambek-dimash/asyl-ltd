from django.conf import settings
from django.db import models

from .managers import ClientManager


class Client(models.Model):
    CURRENCIES = (("KZT", "KZT (тенге)"), ("USD", "USD (доллар)"))
    company_name = models.CharField(
        "Название ТОО / ИП", max_length=200, blank=True, default="")
    phone = models.CharField(max_length=50)
    country = models.CharField(max_length=100, blank=True, default="")
    iin = models.CharField("ИИН/БИН", max_length=20, blank=True, default="")
    bank = models.CharField("Банк", max_length=150, blank=True, default="")
    bank_account = models.CharField("Расчётный счёт", max_length=34, blank=True, default="")
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="KZT")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="client_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ClientManager()

    @property
    def name(self) -> str:
        return self.user.get_full_name() or self.user.username

    def __str__(self):
        return self.name


class Store(models.Model):
    SCHEDULE_TYPES = ["none", "monthly", "weekly"]

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="stores"
    )
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    payment_schedule_type = models.CharField(max_length=20, default="none")
    payment_days = models.JSONField(default=list, blank=True)
    contract_signed_at = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name
