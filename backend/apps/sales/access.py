from django.db.models import Q


def assigned_department_id(user) -> int | None:
    if getattr(user, "is_superuser", False):
        return None
    employee = getattr(user, "employee", None)
    return getattr(employee, "sales_department_id", None)


def scope_by_client_department(queryset, user, client_path="", *, unassigned=None, shared=None):
    """Строки клиентов отдела сотрудника.

    ``unassigned`` — условие (Q) на строки клиентов без отдела, которые видны
    любому отделу: это общая очередь, из которой отдел забирает клиента к себе.

    ``shared`` — условие (Q или Exists) на строки ЛЮБОГО отдела, открытые всем
    отделам: общая очередь подтверждения кассы («Заявки и оплаты»).
    """
    department_id = assigned_department_id(user)
    if department_id is None:
        return queryset
    prefix = f"{client_path}__" if client_path else ""
    visible = Q(**{f"{prefix}department_id": department_id})
    if unassigned is not None:
        visible |= Q(**{f"{prefix}department__isnull": True}) & unassigned
    if shared is not None:
        visible |= shared
    return queryset.filter(visible)
