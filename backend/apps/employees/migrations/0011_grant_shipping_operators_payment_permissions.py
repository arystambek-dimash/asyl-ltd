from django.db import migrations


PAYMENT_PERMISSIONS = (
    {
        "code": "payments.view",
        "section": "payments",
        "action": "view",
        "label": "Оплаты: Просмотр",
    },
    {
        "code": "payments.create",
        "section": "payments",
        "action": "create",
        "label": "Оплаты: Создание",
    },
)


def grant_shipping_operators_payment_permissions(apps, schema_editor):
    """Extend the existing operational capability, never a free-form title.

    Roles were retired and ``position`` is only a display label.  ``shipping.load``
    is the durable authorization signal for employees who work at the loading
    post, so the migration grants those same employees the ability to record a
    CRM payment without teaching runtime authorization about job titles.
    """
    Employee = apps.get_model("employees", "Employee")
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias

    # Normally these rows already exist through the RBAC dependency.  Ensuring
    # the two target rows also makes this additive migration robust after a
    # partial data restore, without replacing or removing any assignment.
    payment_permissions = [
        Permission.objects.using(database).update_or_create(
            code=definition["code"], defaults=definition,
        )[0]
        for definition in PAYMENT_PERMISSIONS
    ]
    shipping_load = (
        Permission.objects.using(database).filter(code="shipping.load").first()
    )
    if shipping_load is None:
        # No employee can currently hold a relation to a missing permission.
        return

    employees = Employee.objects.using(database).filter(
        permissions=shipping_load
    ).distinct()
    for employee in employees.iterator():
        employee.permissions.add(*payment_permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0010_department_move_complete"),
        ("rbac", "0020_client_manage_access_permission"),
    ]

    operations = [
        migrations.RunPython(
            grant_shipping_operators_payment_permissions,
            migrations.RunPython.noop,
        ),
    ]
