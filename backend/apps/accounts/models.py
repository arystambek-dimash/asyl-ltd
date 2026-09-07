from functools import cached_property

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)

    @cached_property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from apps.sys_permissions.perms import ALL_CODES
            return set(ALL_CODES)
        emp = getattr(self, "employee", None)

        if emp is None or not emp.is_active:
            return set()
        return {permission.code for permission in emp.permissions.all()}

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        return code in self.perm_codes
