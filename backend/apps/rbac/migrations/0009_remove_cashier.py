from django.db import migrations


def forward(apps, schema_editor):
    """Касса и бухгалтер — один человек: убираем отдельную кассовую стадию.

    - Сотрудников роли «Кассир» переносим на роль «Касса» (бывш. «Бухгалтер»).
    - Удаляем право payments.cashier из ролей, личных наборов и каталога.
    - Ресидим каталог и системные роли из единого источника (perms.py).
    """
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    Employee = apps.get_model("employees", "Employee")
    from apps.rbac.perms import PERMISSIONS, PRESETS

    # 1) Роль «Бухгалтер» переименовываем в «Касса» (сохраняем назначения).
    buh = Role.objects.filter(name="Бухгалтер").first()
    kassa = Role.objects.filter(name="Касса").first()
    if buh and not kassa:
        buh.name = "Касса"
        buh.save(update_fields=["name"])
        kassa = buh
    if kassa is None:
        kassa, _ = Role.objects.get_or_create(name="Касса", defaults={"is_system": True})

    # 2) Сотрудников старой роли «Кассир» переводим на «Касса», затем удаляем роль.
    cashier_role = Role.objects.filter(name="Кассир").first()
    if cashier_role:
        Employee.objects.filter(role=cashier_role).update(role=kassa)
        cashier_role.permissions.clear()
        cashier_role.delete()

    # 3) Убираем право payments.cashier отовсюду и из каталога.
    cashier_perm = Permission.objects.filter(code="payments.cashier").first()
    if cashier_perm:
        cashier_perm.roles.clear()
        cashier_perm.employees.clear()
        cashier_perm.delete()

    # 4) Идемпотентный ресид каталога и системных ролей из единого источника.
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)
    for name, codes in PRESETS.items():
        role, _ = Role.objects.get_or_create(name=name, defaults={"is_system": True})
        role.is_system = True
        role.save()
        role.permissions.set(Permission.objects.filter(code__in=codes))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0008_controller_role"),
        ("employees", "0004_inherit_role_permissions"),
    ]
    operations = [migrations.RunPython(forward, noop)]
