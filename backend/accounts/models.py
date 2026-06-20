from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    def _in_group(self, name: str) -> bool:
        return self.groups.filter(name=name).exists()

    @property
    def is_manager(self) -> bool:
        return self._in_group("manager")

    @property
    def is_accountant(self) -> bool:
        return self._in_group("accountant")

    @property
    def is_operator(self) -> bool:
        return self._in_group("operator")

    @property
    def is_boss(self) -> bool:
        return self._in_group("boss")
