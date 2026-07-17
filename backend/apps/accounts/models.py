from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    # Права = права роли ∪ личные права сотрудника: правка роли действует сразу.
    @property
    def _employee(self):
        return getattr(self, "employee", None)

    @property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from apps.rbac.perms import ALL_CODES
            return set(ALL_CODES)
        emp = self._employee
        # Деактивированный сотрудник теряет все права, даже если роль осталась.
        if emp is None or not emp.is_active:
            return set()
        return emp.effective_perm_codes

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        emp = self._employee
        return (emp is not None and emp.is_active
                and code in emp.effective_perm_codes)
