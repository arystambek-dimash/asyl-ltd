from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event
from apps.sales.access import assigned_department_id
from apps.sales.models import Department

from .models import Client


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
    log_event(
        "client",
        f"Клиент «{client.name}» закреплён за отделом «{department.name}»",
        user=user,
        payload={
            "client_id": client.pk,
            "action": "client_department_changed",
            "department_from": None,
            "department_to": department.code,
        },
    )
    return client
