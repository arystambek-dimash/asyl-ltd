from django.db import models
from django.utils import timezone

from apps.common.crypto import SecretDecryptError, decrypt_secret, encrypt_secret


class Department(models.Model):
    """Динамический справочник отделов продаж."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#315FD5")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Kaspi отдела — одно из подключений ApiPay; одно подключение может
    # обслуживать несколько отделов. Без подключения счета и QR не выдаются.
    apipay_account = models.ForeignKey(
        "sales.ApiPayAccount", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="departments",
    )

    class Meta:
        # Историческое имя таблицы сохраняется намеренно: перенос между
        # Django-app не должен трогать production-данные и существующие FK.
        db_table = "clients_department"
        ordering = ["created_at", "id"]

    @property
    def apipay_configured(self) -> bool:
        account = self.apipay_account
        return bool(account and account.api_key)

    def __str__(self):
        return self.name


class ApiPayAccount(models.Model):
    """Подключение Kaspi через ApiPay: ключ и секрет вебхука одного кабинета.

    Хранятся только зашифрованными. Отделы выбирают подключение, поэтому один
    кабинет ApiPay может принимать оплату сразу за несколько отделов.
    """

    name = models.CharField(max_length=100)
    api_key_encrypted = models.TextField(blank=True, default="")
    webhook_secret_encrypted = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    @staticmethod
    def _reveal(token: str) -> str:
        try:
            return decrypt_secret(token)
        except SecretDecryptError:
            # SECRET_KEY сменили без fallback: ключ считается не заданным,
            # суперюзер вводит его заново.
            return ""

    @property
    def api_key(self) -> str:
        return self._reveal(self.api_key_encrypted)

    @property
    def webhook_secret(self) -> str:
        return self._reveal(self.webhook_secret_encrypted)

    def set_api_key(self, value: str) -> None:
        self.api_key_encrypted = encrypt_secret((value or "").strip())
        self.updated_at = timezone.now()

    def set_webhook_secret(self, value: str) -> None:
        self.webhook_secret_encrypted = encrypt_secret((value or "").strip())
        self.updated_at = timezone.now()

    def __str__(self):
        return self.name
