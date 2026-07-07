from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    # Права назначаются сотруднику персонально; роль — только назначение
    # (ярлык и шаблон прав при создании учётки).
    @property
    def _employee(self):
        return getattr(self, "employee", None)

    @property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from apps.rbac.perms import ALL_CODES
            return set(ALL_CODES)
        emp = self._employee
        if emp is None:
            return set()
        return set(emp.permissions.values_list("code", flat=True))

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        emp = self._employee
        return emp is not None and emp.permissions.filter(code=code).exists()
