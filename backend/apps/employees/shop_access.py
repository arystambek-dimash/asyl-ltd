"""Чей магазин: включённый магазин сотрудника и запрет «на себя».

Лёгкий модуль без сервисов заказов: его читают ``/auth/me/`` и оплаты с
ценами заказов (``orders.services``), которым ``employees.shop`` дал бы цикл импортов.
"""

from rest_framework.exceptions import PermissionDenied

from .models import EmployeeShop


def active_shop(user) -> EmployeeShop | None:
    """Включённый магазин действующего сотрудника ``user``; иначе None."""
    if not getattr(user, "is_authenticated", False) or user.is_client:
        return None
    return (
        EmployeeShop.objects.select_related("client__user", "client__department", "warehouse")
        .filter(employee__user=user, employee__is_active=True, is_active=True)
        .first()
    )


def forbid_own_shop(user, client_id, action: str) -> None:
    """Запрет «на себя»: оплаты своего магазина и цены уже оформленных заказов
    сотрудник не оформляет — это делает другой. Прайс на будущие заказы с правом
    на цены ставит сам. Касается и выключенного магазина: долг остаётся его."""
    if not client_id or not getattr(user, "is_authenticated", False):
        return
    if EmployeeShop.objects.filter(client_id=client_id, employee__user=user).exists():
        raise PermissionDenied({
            "detail": f"Это ваш магазин: {action} оформляет другой сотрудник",
            "code": "own_shop",
        })
