from django.db import migrations


def forward(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    # Права переезжают с роли на сотрудника: роль остаётся назначением.
    for emp in Employee.objects.select_related("role").prefetch_related("role__permissions"):
        if emp.role_id:
            emp.permissions.set(emp.role.permissions.all())


def backward(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    for emp in Employee.objects.all():
        emp.permissions.clear()


class Migration(migrations.Migration):
    dependencies = [("employees", "0002_employee_permissions")]
    operations = [migrations.RunPython(forward, backward)]
