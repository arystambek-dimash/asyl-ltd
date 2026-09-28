"""Раздел прав «WhatsApp-бот» → «Telegram-бот»: коды и выданные права те же, меняются подписи."""

from typing import ClassVar

from django.db import migrations

ACTIONS = {"view": "Журнал сообщений", "manage": "Провести и пропустить сообщение"}


def relabel(section_label):
    def forwards(apps, schema_editor):
        Permission = apps.get_model("rbac", "Permission")
        permissions = Permission.objects.using(schema_editor.connection.alias)
        for action, label in ACTIONS.items():
            permissions.filter(code=f"bots.{action}").update(label=f"{section_label}: {label}")

    return forwards


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0029_dashboard_tasks_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(relabel("Telegram-бот"), relabel("WhatsApp-бот")),
    ]
