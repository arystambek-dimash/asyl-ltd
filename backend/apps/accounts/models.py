from functools import cached_property

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)

    @property
    def active_monoblock_device(self):
        device = getattr(self, "monoblock_device", None)
        return device if device is not None and device.is_active and self.is_active else None

    @property
    def is_monoblock(self) -> bool:
        return self.active_monoblock_device is not None

    @cached_property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from apps.sys_permissions.perms import ALL_CODES
            return set(ALL_CODES)
        if self.is_monoblock:
            return {"orders.view", "shipping.load"}
        emp = getattr(self, "employee", None)

        if emp is None or not emp.is_active:
            return set()
        return {permission.code for permission in emp.permissions.all()}

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        if self.is_monoblock:
            return code in {"orders.view", "shipping.load"}
        return code in self.perm_codes
