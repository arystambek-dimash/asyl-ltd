"""Событие ApiPay привязано к подключению, чьим секретом подписано.

Колонка ``department_id`` убирается только из состояния модели
(expand/contract): физически её удалит отдельная миграция позже.
"""
from django.db import migrations, models
from django.db.models import deletion


def link_events_to_accounts(apps, schema_editor):
    ApiPayWebhookEvent = apps.get_model("orders", "ApiPayWebhookEvent")
    Department = apps.get_model("sales", "Department")
    for department in Department.objects.exclude(apipay_account=None):
        ApiPayWebhookEvent.objects.filter(department=department).update(
            apipay_account_id=department.apipay_account_id
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0056_goods_return_without_money"),
        ("sales", "0005_apipay_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="apipay_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=deletion.SET_NULL,
                related_name="webhook_events",
                to="sales.apipayaccount",
            ),
        ),
        migrations.RunPython(link_events_to_accounts, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="apipaywebhookevent", name="department"),
            ],
            database_operations=[],
        ),
    ]
