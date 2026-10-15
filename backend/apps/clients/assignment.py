from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event
from apps.sales.access import assigned_department_id
from apps.sales.models import Department

from .models import Client
from .services import assert_no_active_loading, lock_client_orders, lock_scoped_client


def log_department_change(
    client: Client,
    previous: Department | None,
    user,
    message: str,
) -> None:
    """Событие «клиент перенесён в отдел»: ``previous`` → ``client.department``."""
    current = client.department
    log_event(
        "client",
        message,
        user=user,
        payload={
            "client_id": client.pk,
            "action": "client_department_changed",
            "department_from": previous.code if previous else None,
            "department_to": current.code if current else None,
        },
    )


def assign_client_department(client: Client, department: Department | None, user) -> Client:
    """Закрепить клиента без отдела (саморегистрация) за отделом продаж.

    Вызывающий держит ``select_for_update`` на строке клиента. Сотрудник с
    закреплённым отделом забирает клиента только к себе; клиента, у которого
    отдел уже есть, так не перехватить — перенос идёт через карточку клиента.
    """
    if client.department_id is not None:
        raise ValidationError({
            "detail": "Клиент уже закреплён за отделом",
            "code": "client_already_assigned",
        })
    if department is None or not department.is_active:
        raise ValidationError({"department": "Выберите действующий отдел продаж"})
    own_department_id = assigned_department_id(user)
    if own_department_id is not None and own_department_id != department.pk:
        raise PermissionDenied("Клиента можно закрепить только за своим отделом")
    client.department = department
    client.save(update_fields=["department"])
    log_department_change(
        client,
        None,
        user,
        f"Клиент «{client.name}» закреплён за отделом «{department.name}»",
    )
    return client


def move_client_department(client_pk, department: Department, user, message: str) -> Client:
    """Перенести клиента в отдел ``department`` не из его карточки (магазин
    сотрудника едет за отделом сотрудника); вызывать внутри ``transaction.atomic()``.

    Те же блокировки и проверка, что у переноса в карточке клиента: заказы →
    клиент → заказы, и ни одной идущей погрузки. Возвращает клиента под блокировкой.
    """
    lock_client_orders(client_pk)
    client = lock_scoped_client(client_pk)
    if client.department_id == department.pk:
        return client
    assert_no_active_loading(lock_client_orders(client_pk))
    previous = client.department
    client.department = department
    client.save(update_fields=["department"])
    log_department_change(client, previous, user, message)
    return client
