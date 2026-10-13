"""Страница «ИИ-ассистент»: право задавать вопросы по данным системы.

Страница новая — доступ никто не теряет. Ассистент читает всю базу без учёта
отдела, поэтому право никому не выдаётся автоматически: суперпользователь
видит страницу сразу, остальным право дают в «Сотрудниках».
"""

from typing import ClassVar

from django.db import migrations


def add_assistant_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.using(schema_editor.connection.alias).update_or_create(
        code="assistant.view",
        defaults={
            "section": "assistant",
            "action": "view",
            "label": "ИИ-ассистент: Вопросы по всем данным системы (только чтение)",
        },
    )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0031_storekeeper_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_assistant_permission, migrations.RunPython.noop),
    ]
