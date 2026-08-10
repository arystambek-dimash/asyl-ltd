from django.db import migrations, models


def move_names_to_user(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias

    employees = list(
        Employee.objects.using(database)
        .select_related("user")
        .only(
            "user_id",
            "first_name",
            "last_name",
            "user__first_name",
            "user__last_name",
        )
        .iterator(chunk_size=1000)
    )
    conflicts = []
    for employee in employees:
        for field in ("first_name", "last_name"):
            employee_value = getattr(employee, field)
            user_value = getattr(employee.user, field)
            if user_value and user_value != employee_value:
                conflicts.append(
                    f"employee={employee.pk}/user={employee.user_id}/{field}"
                )

    if conflicts:
        details = ", ".join(conflicts[:20])
        if len(conflicts) > 20:
            details += f", and {len(conflicts) - 20} more"
        raise RuntimeError(
            "Employee/User name conflicts detected; resolve them before migration: "
            + details
        )

    users = []
    for employee in employees:
        employee.user.first_name = employee.first_name
        employee.user.last_name = employee.last_name
        users.append(employee.user)
    if users:
        apps.get_model("accounts", "User").objects.using(database).bulk_update(
            users,
            ["first_name", "last_name"],
            batch_size=1000,
        )


def restore_names_to_employee(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias

    employees = list(
        Employee.objects.using(database)
        .select_related("user")
        .only("id", "first_name", "last_name", "user__first_name", "user__last_name")
        .iterator(chunk_size=1000)
    )
    oversized = [
        employee.pk
        for employee in employees
        if len(employee.user.first_name) > 100 or len(employee.user.last_name) > 100
    ]
    if oversized:
        raise RuntimeError(
            "Cannot restore Employee names longer than 100 characters for employees: "
            + ", ".join(map(str, oversized[:20]))
        )
    for employee in employees:
        employee.first_name = employee.user.first_name
        employee.last_name = employee.user.last_name
    if employees:
        Employee.objects.using(database).bulk_update(
            employees,
            ["first_name", "last_name"],
            batch_size=1000,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0007_flatten_role_permissions"),
    ]

    operations = [
        # Keep the old columns as nullable rollback data for one deployment
        # phase. New runtime code no longer reads or writes them.
        migrations.AlterField(
            model_name="employee",
            name="first_name",
            field=models.CharField(max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="employee",
            name="last_name",
            field=models.CharField(max_length=100, null=True),
        ),
        migrations.RunPython(move_names_to_user, restore_names_to_employee),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="employee",
                    name="first_name",
                ),
                migrations.RemoveField(
                    model_name="employee",
                    name="last_name",
                ),
            ],
        ),
    ]
