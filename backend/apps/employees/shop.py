"""Настройки магазина сотрудника и его клиента для пополнения с мельницы."""

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from apps.clients.assignment import move_client_department
from apps.clients.managers import sync_user_names
from apps.clients.models import Client
from apps.clients.services import lock_client_with_orders
from apps.sales.models import Department
from apps.warehouse.services import resolve_warehouse

from .models import EmployeeShop


def default_shop_name(employee) -> str:
    return f"Магазин {employee.name}"


def active_shop(user) -> EmployeeShop | None:
    """Включённый магазин действующего сотрудника ``user``; иначе None."""
    if not getattr(user, "is_authenticated", False) or user.is_client:
        return None
    return (
        EmployeeShop.objects.select_related("client__user", "client__department", "warehouse")
        .filter(employee__user=user, employee__is_active=True, is_active=True)
        .first()
    )


def require_shop(user) -> EmployeeShop:
    shop = active_shop(user)
    if shop is None:
        raise NotFound("У вас нет своего магазина")
    return shop


def _shop_department(employee) -> Department:
    """Отдел магазина — отдел сотрудника (иначе его заказ был бы «чужим»),
    без отдела — основной отдел продаж."""
    if employee.sales_department_id:
        return employee.sales_department
    department = (
        Department.objects.filter(is_active=True).order_by("-is_default", "created_at", "id").first()
    )
    if department is None:
        raise ValidationError({"shop": "Нет действующего отдела продаж для заказов магазина"})
    return department


def _lock_shop_client(employee):
    # Совпадает с отгрузкой и переносом клиента: Order → Client → EmployeeShop.
    client_id = EmployeeShop.objects.filter(employee=employee).values_list("client_id", flat=True).first()
    if client_id is not None:
        lock_client_with_orders(client_id, None)


@transaction.atomic
def set_employee_shop(employee, user, *, enabled: bool, name: str = "", warehouse=None) -> EmployeeShop | None:
    """Включить, настроить или выключить магазин сотрудника.

    Первое включение заводит покупателя-магазин (клиента с выключенным входом в
    портал) в отделе сотрудника. Выключение только прячет «Заказы магазина»: клиент,
    заказы и долг остаются, а повторное включение берёт того же клиента.
    ``warehouse`` — склад пополнения магазина; ``None`` — прежний,
    у нового магазина — основной.
    """
    _lock_shop_client(employee)
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client__user", "client__department", "warehouse")
        .filter(employee=employee)
        .first()
    )
    if not enabled:
        if shop is not None and shop.is_active:
            shop.is_active = False
            shop.save(update_fields=["is_active"])
        return shop

    name = " ".join(str(name or "").split()) or (shop.client.name if shop else default_shop_name(employee))
    warehouse = resolve_warehouse(warehouse if warehouse is not None else (shop.warehouse if shop else None))
    department = _shop_department(employee)
    if shop is None:
        client = Client.objects.create_with_user(
            first_name=name, last_name="", phone=employee.phone, department=department,
        )
        return EmployeeShop.objects.create(employee=employee, client=client, warehouse=warehouse)

    sync_user_names(shop.client.user, {"first_name": name, "last_name": ""})
    _move_with_employee(shop, department, user)
    shop.warehouse, shop.is_active = warehouse, True
    shop.save(update_fields=["warehouse", "is_active"])
    return shop


def _move_with_employee(shop: EmployeeShop, department: Department, user) -> None:
    if shop.client.department_id == department.pk:
        return
    shop.client = move_client_department(
        shop.client_id, department, user,
        f"Магазин «{shop.client.name}» переведён в отдел «{department.name}» вместе с сотрудником",
    )


@transaction.atomic
def follow_employee_department(employee, user) -> None:
    """Сотрудника перевели в другой отдел — его включённый магазин едет за ним:
    иначе заказ магазина оказался бы «чужим». Склад и название не трогает."""
    _lock_shop_client(employee)
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client")
        .filter(employee=employee, is_active=True)
        .first()
    )
    if shop is not None:
        _move_with_employee(shop, _shop_department(employee), user)


def shop_settings(shop: EmployeeShop | None) -> dict | None:
    """Настройки магазина в карточке сотрудника."""
    if shop is None:
        return None
    return {
        "enabled": shop.is_active,
        "client": shop.client_id,
        "name": shop.client.name,
        "warehouse": shop.warehouse_id,
        "warehouse_name": shop.warehouse.name,
    }
