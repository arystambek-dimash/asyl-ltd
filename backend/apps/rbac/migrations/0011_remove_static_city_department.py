from django.db import migrations


def remove_static_department_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    Employee = apps.get_model("employees", "Employee")
    from apps.rbac.perms import PERMISSIONS, PRESETS

    manager, _ = Role.objects.get_or_create(name="Менеджер", defaults={"is_system": True})
    old_role = Role.objects.filter(name="Менеджер Сити").first()
    if old_role:
        Employee.objects.filter(role=old_role).update(role=manager)
        old_role.permissions.clear()
        old_role.delete()

    old_permissions = Permission.objects.filter(code__startswith="dept2.")
    for permission in old_permissions:
        permission.roles.clear()
        permission.employees.clear()
    old_permissions.delete()

    for payload in PERMISSIONS:
        Permission.objects.update_or_create(code=payload["code"], defaults=payload)
    for name, codes in PRESETS.items():
        role, _ = Role.objects.get_or_create(name=name, defaults={"is_system": True})
        role.is_system = True
        role.save(update_fields=["is_system"])
        role.permissions.set(Permission.objects.filter(code__in=codes))


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0010_client_price_permission"),
        ("employees", "0004_inherit_role_permissions"),
    ]

    operations = [
        migrations.RunPython(remove_static_department_permissions, migrations.RunPython.noop),
    ]
