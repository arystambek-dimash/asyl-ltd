from functools import cached_property

from django.conf import settings
from django.db import models


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee"
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=50, blank=True, default="")
    position = models.CharField(max_length=100, blank=True, default="")
    # Роль даёт права «вживую»: правка роли сразу действует на всех её сотрудников.
    role = models.ForeignKey(
        "rbac.Role", null=True, blank=True, on_delete=models.PROTECT, related_name="employees"
    )
    # Личные дополнительные доступы поверх роли.
    permissions = models.ManyToManyField(
        "rbac.Permission", blank=True, related_name="employees"
    )
    # Точечные запреты поверх роли. Это позволяет, например, оставить
    # сотруднику роль «Менеджер», но отозвать только удаление товаров.
    denied_permissions = models.ManyToManyField(
        "rbac.Permission", blank=True, related_name="denied_for_employees"
    )
    is_active = models.BooleanField(default=True)

    @cached_property
    def effective_perm_codes(self) -> set:
        """(Права роли − личные запреты) ∪ личные права."""
        codes = set(self.permissions.values_list("code", flat=True))
        if self.role_id:
            denied = set(self.denied_permissions.values_list("code", flat=True))
            codes |= set(self.role.permissions.values_list("code", flat=True)) - denied
        return codes

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.name
