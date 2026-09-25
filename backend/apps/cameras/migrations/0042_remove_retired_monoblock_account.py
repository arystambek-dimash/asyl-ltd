from django.db import migrations

ARCHIVE_TABLE = "cameras_monoblockdevice"
USER_COLUMNS = ("user_id", "created_by_id")


def _user_foreign_keys(schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, ARCHIVE_TABLE)
    return {
        info["columns"][0]: name
        for name, info in constraints.items()
        if info["foreign_key"] and len(info["columns"]) == 1 and info["columns"][0] in USER_COLUMNS
    }


def drop_user_foreign_keys(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    quote = schema_editor.quote_name
    for name in _user_foreign_keys(schema_editor).values():
        schema_editor.execute(f"ALTER TABLE {quote(ARCHIVE_TABLE)} DROP CONSTRAINT {quote(name)}")


def restore_user_foreign_keys(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    model = apps.get_model("cameras", "RetiredMonoblockAccount")
    existing = _user_foreign_keys(schema_editor)
    for field_name in ("user", "created_by"):
        field = model._meta.get_field(field_name)
        if field.column in existing:
            continue
        schema_editor.execute(schema_editor._create_fk_sql(model, field, "_fk_%(to_table)s_%(to_column)s"))


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0041_event_cursor_drop_snapshot_compat"),
    ]

    operations = [
        # Expand/contract rollout: архив технических аккаунтов моноблока
        # уходит из состояния Django, таблица остаётся для образа автоотката.
        # Ключи на пользователей снимаются: новый образ про таблицу не знает,
        # и ни удаление пользователя, ни очистка базы в тестах не должны
        # упираться в архив, который никто не читает.
        migrations.RunPython(drop_user_foreign_keys, restore_user_foreign_keys, elidable=False),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="RetiredMonoblockAccount"),
            ],
            database_operations=[],
        ),
    ]
