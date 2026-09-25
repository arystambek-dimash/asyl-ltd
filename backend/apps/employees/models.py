from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee"
    )
    phone = models.CharField(max_length=50, blank=True, default="")
    position = models.CharField(max_length=100, blank=True, default="")
    sales_department = models.ForeignKey(
        "sales.Department",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_employees",
    )
    permissions = models.ManyToManyField(
        "rbac.Permission",
        blank=True,
        related_name="employees"
    )
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is None or "is_active" in update_fields:
            self._sync_user_active()

    def _sync_user_active(self):
        # Вход в систему следует флагу сотрудника: выключенный сотрудник
        # не должен логиниться, откуда бы флаг ни поменяли (API, админка).
        get_user_model().objects.filter(pk=self.user_id).exclude(
            is_active=self.is_active
        ).update(is_active=self.is_active)
        if self._meta.get_field("user").is_cached(self):
            self.user.is_active = self.is_active

    @property
    def name(self) -> str:
        return self.user.get_full_name() or self.user.username

    def __str__(self):
        return self.name
