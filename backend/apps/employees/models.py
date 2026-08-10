from django.conf import settings
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

    @property
    def name(self) -> str:
        return self.user.get_full_name() or self.user.username

    def __str__(self):
        return self.name
