from django.db import migrations


def forward(apps, schema_editor):
    """Права роли теперь наследуются вживую: снимаем с сотрудников копии,
    сделанные миграцией 0003, чтобы правка роли действовала на всех."""
    Employee = apps.get_model("employees", "Employee")
    for emp in (Employee.objects.exclude(role=None)
                .prefetch_related("permissions", "role__permissions")):
        role_ids = {p.id for p in emp.role.permissions.all()}
        dup = [p for p in emp.permissions.all() if p.id in role_ids]
        if dup:
            emp.permissions.remove(*dup)


def backward(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    for emp in Employee.objects.exclude(role=None).prefetch_related("role__permissions"):
        emp.permissions.add(*emp.role.permissions.all())


class Migration(migrations.Migration):
    dependencies = [("employees", "0003_copy_role_permissions")]
    operations = [migrations.RunPython(forward, backward)]
