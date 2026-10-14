from django.db import models

from apps.common.models import SingletonModel


class DashboardSettings(SingletonModel):
    """Что видно на «Главной» и на ТВ: отделы и склады. Меняет суперпользователь.

    Пустой список — видно всё. Права не сужает: это выбор экрана, а не доступ.
    """

    departments = models.ManyToManyField("sales.Department", blank=True, related_name="+")
    warehouses = models.ManyToManyField("warehouse.Warehouse", blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
