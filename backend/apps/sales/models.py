from django.db import models


class Department(models.Model):
    """Динамический справочник отделов продаж."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#315FD5")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

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

    def __str__(self):
        return self.name
