"""Операции миграций, которые переживают откат образа.

Автооткат образа не откатывает миграции. Django 5.2
создаёт внешние ключи без ``ON DELETE`` — каскад и обнуление делает ORM. Старый
образ не знает о новой таблице: его ``delete()`` строки, на которую она
ссылается (откат отгрузки, удаление товара), падает на проверке ключа при
коммите. Поэтому ключ НОВОЙ таблицы на СТАРУЮ повторяет ``on_delete`` в базе.

Операция идёт отдельной миграцией после ``CreateModel``: ключи новой таблицы
Django добавляет отложенным SQL в конце своей миграции.
"""

from django.db import migrations

DB_ON_DELETE_ACTIONS = ("CASCADE", "SET NULL")


def db_on_delete(app_label: str, model_name: str, field_name: str, action: str) -> migrations.RunPython:
    """Пересоздать внешний ключ ``field_name`` с ``ON DELETE action`` (только PostgreSQL)."""
    if action not in DB_ON_DELETE_ACTIONS:
        raise ValueError(f"ON DELETE {action} не поддерживается")

    def forwards(apps, schema_editor):
        connection = schema_editor.connection
        if connection.vendor != "postgresql":
            return
        model = apps.get_model(app_label, model_name)
        field = model._meta.get_field(field_name)
        table, column = model._meta.db_table, field.column
        target = field.target_field
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        names = [
            name for name, info in constraints.items()
            if info["foreign_key"] and info["columns"] == [column]
        ]
        if len(names) != 1:
            raise RuntimeError(f"{table}.{column}: ожидался один внешний ключ, найдено {len(names)}")
        quote = schema_editor.quote_name
        schema_editor.execute(f"ALTER TABLE {quote(table)} DROP CONSTRAINT {quote(names[0])}")
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(names[0])} "
            f"FOREIGN KEY ({quote(column)}) "
            f"REFERENCES {quote(target.model._meta.db_table)} ({quote(target.column)}) "
            f"ON DELETE {action} DEFERRABLE INITIALLY DEFERRED"
        )

    # Обратно — ничего: ключ с ON DELETE строже ORM не делает, а таблицу
    # снимает обратная операция её CreateModel.
    return migrations.RunPython(forwards, migrations.RunPython.noop, elidable=False)
