from django.db import migrations


RENAMES = {
    "rbac.view": (
        "sys_permissions.view",
        "sys_permissions",
        "Системные права: Просмотр",
    ),
    "rbac.manage": (
        "sys_permissions.manage",
        "sys_permissions",
        "Системные права: Управление",
    ),
}

LEGACY_TABLE_RENAMES = {
    "rbac_role": "legacy_rbac_role",
    "rbac_role_permissions": "legacy_rbac_role_permissions",
    "employees_employee_denied_permissions": (
        "legacy_employees_employee_denied_permissions"
    ),
}

RESTORED_FOREIGN_KEYS = (
    (
        "rbac_role_permissions",
        "rbac_role_perm_permission_restore_fk",
        "permission_id",
        "rbac_permission",
    ),
    (
        "employees_employee_denied_permissions",
        "employee_denied_employee_restore_fk",
        "employee_id",
        "employees_employee",
    ),
    (
        "employees_employee_denied_permissions",
        "employee_denied_permission_restore_fk",
        "permission_id",
        "rbac_permission",
    ),
)


def rename_permission_codes(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    for old_code, (new_code, section, label) in RENAMES.items():
        old_permission = Permission.objects.using(database).filter(code=old_code).first()
        new_permission = Permission.objects.using(database).filter(code=new_code).first()
        if old_permission is not None and new_permission is not None:
            raise RuntimeError(
                f"Both {old_code!r} and {new_code!r} exist; refusing a lossy merge"
            )
        if old_permission is not None:
            old_permission.code = new_code
            old_permission.section = section
            old_permission.label = label
            old_permission.save(update_fields=["code", "section", "label"])
        elif new_permission is not None:
            Permission.objects.using(database).filter(pk=new_permission.pk).update(
                section=section,
                label=label,
            )


def restore_permission_codes(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    for old_code, (new_code, _section, _label) in RENAMES.items():
        permission = Permission.objects.using(database).filter(code=new_code).first()
        if permission is None:
            continue
        if Permission.objects.using(database).filter(code=old_code).exists():
            raise RuntimeError(
                f"Both {old_code!r} and {new_code!r} exist; refusing a lossy merge"
            )
        permission.code = old_code
        permission.section = "rbac"
        permission.label = (
            "Доступы: Просмотр" if old_code.endswith(".view")
            else "Доступы: Управление"
        )
        permission.save(update_fields=["code", "section", "label"])


def _rename_table(schema_editor, old_name, new_name):
    quote = schema_editor.quote_name
    schema_editor.execute(
        f"ALTER TABLE {quote(old_name)} RENAME TO {quote(new_name)}"
    )


def _drop_runtime_foreign_keys(schema_editor, table_name):
    """Detach immutable archive rows from tables still used by the application."""

    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)

    runtime_tables = {"employees_employee", "rbac_permission"}
    quote = schema_editor.quote_name
    for name, details in constraints.items():
        foreign_key = details.get("foreign_key")
        if foreign_key and foreign_key[0] in runtime_tables:
            schema_editor.execute(
                f"ALTER TABLE {quote(table_name)} "
                f"DROP CONSTRAINT {quote(name)}"
            )


def archive_role_tables(apps, schema_editor):
    """Keep production role data as a detached, explicitly named archive."""

    for old_name, new_name in LEGACY_TABLE_RENAMES.items():
        _rename_table(schema_editor, old_name, new_name)

    quote = schema_editor.quote_name
    schema_editor.execute(
        f"ALTER TABLE {quote('employees_employee')} "
        f"RENAME COLUMN {quote('role_id')} TO {quote('legacy_role_id')}"
    )

    _drop_runtime_foreign_keys(
        schema_editor, "legacy_rbac_role_permissions"
    )
    _drop_runtime_foreign_keys(
        schema_editor, "legacy_employees_employee_denied_permissions"
    )


def restore_role_tables(apps, schema_editor):
    """Restore the old physical names for an emergency code rollback."""

    quote = schema_editor.quote_name
    schema_editor.execute(
        f"ALTER TABLE {quote('employees_employee')} "
        f"RENAME COLUMN {quote('legacy_role_id')} TO {quote('role_id')}"
    )
    for old_name, new_name in reversed(LEGACY_TABLE_RENAMES.items()):
        _rename_table(schema_editor, new_name, old_name)

    for table, constraint, column, target_table in RESTORED_FOREIGN_KEYS:
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} "
            f"ADD CONSTRAINT {quote(constraint)} "
            f"FOREIGN KEY ({quote(column)}) "
            f"REFERENCES {quote(target_table)} ({quote('id')}) "
            # Keep every archived row even if its employee/permission was
            # deleted while Role was retired. New writes are still enforced.
            "DEFERRABLE INITIALLY DEFERRED NOT VALID"
        )
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS ("
                f"SELECT 1 FROM {quote(table)} archived "
                f"LEFT JOIN {quote(target_table)} current "
                f"ON archived.{quote(column)} = current.{quote('id')} "
                f"WHERE archived.{quote(column)} IS NOT NULL "
                f"AND current.{quote('id')} IS NULL"
                f")"
            )
            has_orphans = cursor.fetchone()[0]
        if not has_orphans:
            schema_editor.execute(
                f"ALTER TABLE {quote(table)} "
                f"VALIDATE CONSTRAINT {quote(constraint)}"
            )


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0017_silos_permission"),
        ("employees", "0007_flatten_role_permissions"),
    ]

    operations = [
        migrations.RunPython(rename_permission_codes, restore_permission_codes),
        migrations.RunPython(archive_role_tables, restore_role_tables),
        # Delete Role from Django's state/API while retaining every legacy row
        # in detached legacy_* tables. A later release may clean that archive.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="Role")],
        ),
    ]
