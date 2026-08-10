from collections import defaultdict

from django.db import migrations


SALES_REQUIRED = {
    "orders.view": ("orders", "view", "Заказы: Просмотр"),
    "orders.create": ("orders", "create", "Заказы: Создание"),
    "clients.view": ("clients", "view", "Клиенты: Просмотр"),
    "catalog.view": ("catalog", "view", "Товары: Просмотр"),
}


def flatten_effective_permissions(apps, schema_editor):
    """Materialize every employee's current effective access as direct grants."""

    Employee = apps.get_model("employees", "Employee")
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    database = schema_editor.connection.alias

    sales_permission_ids = {}
    for code, (section, action, label) in SALES_REQUIRED.items():
        permission, _ = Permission.objects.using(database).get_or_create(
            code=code,
            defaults={"section": section, "action": action, "label": label},
        )
        sales_permission_ids[code] = permission.pk

    role_permissions = defaultdict(set)
    for role_id, permission_id in (
        Role.permissions.through.objects.using(database)
        .values_list("role_id", "permission_id")
        .iterator(chunk_size=2000)
    ):
        role_permissions[role_id].add(permission_id)

    denied_permissions = defaultdict(set)
    for employee_id, permission_id in (
        Employee.denied_permissions.through.objects.using(database)
        .values_list("employee_id", "permission_id")
        .iterator(chunk_size=2000)
    ):
        denied_permissions[employee_id].add(permission_id)

    direct_through = Employee.permissions.through
    additions = []
    employees = (
        Employee.objects.using(database)
        .select_related("role")
        .only("id", "position", "role_id", "role__name", "sales_department_id")
    )
    for employee in employees.iterator(chunk_size=500):
        inherited = role_permissions[employee.role_id] - denied_permissions[employee.pk]
        if employee.sales_department_id:
            inherited.update(sales_permission_ids.values())
        additions.extend(
            direct_through(employee_id=employee.pk, permission_id=permission_id)
            for permission_id in inherited
        )

        # Role was only a CRM label. Preserve it in the existing position field
        # when that field has no more specific value.
        if employee.role_id and not employee.position.strip():
            Employee.objects.using(database).filter(pk=employee.pk).update(
                position=employee.role.name
            )

    direct_through.objects.using(database).bulk_create(
        additions,
        batch_size=2000,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0006_employee_sales_department"),
        ("rbac", "0017_silos_permission"),
    ]

    operations = [
        migrations.RunPython(flatten_effective_permissions, migrations.RunPython.noop),
        # Runtime fields disappear, but their production columns/tables remain
        # untouched for rollback and audit safety in this release.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="employee",
                    name="denied_permissions",
                ),
                migrations.RemoveField(
                    model_name="employee",
                    name="role",
                ),
            ],
        ),
    ]
