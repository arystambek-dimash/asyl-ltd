from django.conf import settings
from django.db import models


class Department(models.Model):
    """Отдел продаж: код фиксирован (main/field), название редактирует админ."""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Client(models.Model):
    DEPARTMENTS = ["main", "field"]

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=50)
    country = models.CharField(max_length=100, blank=True, default="")
    iin = models.CharField("ИИН/БИН", max_length=20, blank=True, default="")
    bank = models.CharField("Банк", max_length=150, blank=True, default="")
    bank_account = models.CharField("Расчётный счёт", max_length=34, blank=True, default="")
    # Принадлежность к отделу продаж: main — Отдел 1, field — Отдел 2 «Сити».
    department = models.CharField(max_length=10, default="main")
    # Менеджер выездного отдела, который ведёт этого клиента (только для field).
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="managed_clients",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="client_profile",
    )

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

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
