def assigned_department_id(user) -> int | None:
    if getattr(user, "is_superuser", False):
        return None
    employee = getattr(user, "employee", None)
    return getattr(employee, "sales_department_id", None)


def scope_by_client_department(queryset, user, client_path=""):
    department_id = assigned_department_id(user)
    if department_id is None:
        return queryset
    lookup = "__".join(
        part for part in (client_path, "department_id") if part
    )
    return queryset.filter(**{lookup: department_id})
