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
    # У каждого отдела свой аккаунт Kaspi: ключ ApiPay и секрет вебхука
    # хранятся только зашифрованными, общего ключа в окружении больше нет.
    # db_default: таблица общая с историческими миграциями clients, и старые
    # состояния модели вставляют строки без этих колонок.
    apipay_api_key_encrypted = models.TextField(
        blank=True, default="", db_default=""
    )
    apipay_webhook_secret_encrypted = models.TextField(
        blank=True, default="", db_default=""
    )
    apipay_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Историческое имя таблицы сохраняется намеренно: перенос между
        # Django-app не должен трогать production-данные и существующие FK.
        db_table = "clients_department"
        ordering = ["created_at", "id"]

    @classmethod
    def default_code(cls) -> str:
        row = (
            cls.objects.filter(is_active=True, is_default=True).first()
            or cls.objects.filter(is_active=True).first()
        )
        return row.code if row else "main"

    @staticmethod
    def _reveal(token: str) -> str:
        try:
            return decrypt_secret(token)
        except SecretDecryptError:
            # SECRET_KEY сменили без fallback: ключ считается не заданным,
            # суперюзер вводит его заново.
            return ""

    @property
    def apipay_api_key(self) -> str:
        return self._reveal(self.apipay_api_key_encrypted)

    @property
    def apipay_webhook_secret(self) -> str:
        return self._reveal(self.apipay_webhook_secret_encrypted)

    @property
    def apipay_configured(self) -> bool:
        return bool(self.apipay_api_key)

    def set_apipay_api_key(self, value: str) -> None:
        """Пустое значение отключает Kaspi: без ключа секрет вебхука не нужен."""
        self.apipay_api_key_encrypted = encrypt_secret((value or "").strip())
        if not self.apipay_api_key_encrypted:
            self.apipay_webhook_secret_encrypted = ""
        self.apipay_updated_at = timezone.now()

    def set_apipay_webhook_secret(self, value: str) -> None:
        self.apipay_webhook_secret_encrypted = encrypt_secret((value or "").strip())
        self.apipay_updated_at = timezone.now()

    def __str__(self):
        return self.name
