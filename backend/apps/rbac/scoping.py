"""Серверное разграничение данных по отделам продаж.

Отдел 1 (main) — основной отдел; видимость даёт базовое право раздела
(clients.view / orders.view). Отдел 2 «Сити» (field) — выездной отдел:
dept2.view_all открывает все данные отдела (руководитель, бухгалтер, кассир),
dept2.view — только собственные записи менеджера.
"""
from django.db.models import Q

DEPARTMENTS = ["main", "field"]
DEPARTMENT_LABELS = {"main": "Отдел 1", "field": "Сити"}


def scope_by_department(qs, user, base_view_perm, *,
                        dept_field="department", owner_field="manager"):
    """Оставить в queryset только записи отделов, доступных пользователю.

    owner_field — путь до менеджера-владельца записи Отдела 2
    (например "manager" для клиентов, "client__manager" для заказов).
    """
    if not user or not user.is_authenticated or getattr(user, "is_client", False):
        return qs.none()
    if user.is_superuser:
        return qs
    q = Q()
    if user.has_perm_code(base_view_perm):
        q |= Q(**{dept_field: "main"})
    if user.has_perm_code("dept2.view_all"):
        q |= Q(**{dept_field: "field"})
    elif user.has_perm_code("dept2.view"):
        q |= Q(**{dept_field: "field", owner_field: user})
    return qs.filter(q)


def sees_all_departments(user) -> bool:
    """Пользователь видит сводную картину обоих отделов (колонка «Отдел»)."""
    return bool(user) and (user.is_superuser or user.has_perm_code("dept2.view_all"))
