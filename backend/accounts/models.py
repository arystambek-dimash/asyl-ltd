from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    @property
    def _role(self):
        emp = getattr(self, "employee", None)
        return emp.role if emp else None

    @property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from rbac.perms import ALL_CODES
            return set(ALL_CODES)
        role = self._role
        if role is None:
            return set()
        return set(role.permissions.values_list("code", flat=True))

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        role = self._role
        return role is not None and role.permissions.filter(code=code).exists()
