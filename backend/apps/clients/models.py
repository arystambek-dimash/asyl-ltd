from django.conf import settings
from django.db import models


class Department(models.Model):
    """Динамический справочник отделов, выбираемых непосредственно в заказе."""
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#315FD5")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    @classmethod
    def default_code(cls) -> str:
        row = (cls.objects.filter(is_active=True, is_default=True).first()
               or cls.objects.filter(is_active=True).first())
        return row.code if row else "main"

    def __str__(self):
        return self.name


class Client(models.Model):
    CURRENCIES = (("KZT", "KZT (тенге)"), ("USD", "USD (доллар)"))
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    # Юридическое наименование покупателя для счетов и других документов.
    # Оставляем отдельным от ФИО контактного лица: это разные реквизиты.
    company_name = models.CharField(
        "Название ТОО / ИП", max_length=200, blank=True, default="")
    phone = models.CharField(max_length=50)
    country = models.CharField(max_length=100, blank=True, default="")
    iin = models.CharField("ИИН/БИН", max_length=20, blank=True, default="")
    bank = models.CharField("Банк", max_length=150, blank=True, default="")
    bank_account = models.CharField("Расчётный счёт", max_length=34, blank=True, default="")
    # Валюта личного прайс-листа. Меняется для будущих заказов; в созданном
    # заказе код валюты фиксируется отдельным снимком.
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="KZT")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="client_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

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
