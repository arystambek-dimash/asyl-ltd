"""Подключения ApiPay вместо ключа у каждого отдела.

Ключи отделов переезжают в ``ApiPayAccount`` (одинаковые ключи — в одно
подключение), отделы ссылаются на него. Старые колонки отдела очищаются и
убираются только из состояния модели (expand/contract): физически их удалит
отдельная миграция позже, а откат вернёт ключи из подключений обратно.
"""
from django.db import migrations, models
from django.db.models import deletion
from django.utils import timezone

from apps.common.crypto import SecretDecryptError, decrypt_secret


def _reveal(token: str) -> str:
    try:
        return decrypt_secret(token)
    except SecretDecryptError:
        return ""


def move_keys_to_accounts(apps, schema_editor):
    Department = apps.get_model("sales", "Department")
    ApiPayAccount = apps.get_model("sales", "ApiPayAccount")
    accounts_by_key = {}
    for department in Department.objects.exclude(
        apipay_api_key_encrypted=""
    ).order_by("created_at", "id"):
        key = _reveal(department.apipay_api_key_encrypted)
        if not key:
            continue
        account = accounts_by_key.get(key)
        if account is None:
            account = ApiPayAccount.objects.create(
                name=department.name,
                api_key_encrypted=department.apipay_api_key_encrypted,
                webhook_secret_encrypted=department.apipay_webhook_secret_encrypted,
                updated_at=department.apipay_updated_at or timezone.now(),
            )
            accounts_by_key[key] = account
        elif (
            not account.webhook_secret_encrypted
            and department.apipay_webhook_secret_encrypted
        ):
            account.webhook_secret_encrypted = (
                department.apipay_webhook_secret_encrypted
            )
            account.save(update_fields=["webhook_secret_encrypted"])
        department.apipay_account = account
        department.save(update_fields=["apipay_account"])
    # Секреты живут только в подключениях: колонки отдела пустеют, и
    # чтение отдела целиком (ассистент, выгрузки) их больше не видит.
    Department.objects.update(
        apipay_api_key_encrypted="", apipay_webhook_secret_encrypted=""
    )


def restore_department_keys(apps, schema_editor):
    Department = apps.get_model("sales", "Department")
    for department in Department.objects.exclude(
        apipay_account=None
    ).select_related("apipay_account"):
        account = department.apipay_account
        department.apipay_api_key_encrypted = account.api_key_encrypted
        department.apipay_webhook_secret_encrypted = (
            account.webhook_secret_encrypted
        )
        department.apipay_updated_at = account.updated_at
        department.save(update_fields=[
            "apipay_api_key_encrypted",
            "apipay_webhook_secret_encrypted",
            "apipay_updated_at",
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0004_seed_apipay_from_env"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiPayAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("api_key_encrypted", models.TextField(blank=True, default="")),
                ("webhook_secret_encrypted", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddField(
            model_name="department",
            name="apipay_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=deletion.SET_NULL,
                related_name="departments",
                to="sales.apipayaccount",
            ),
        ),
        migrations.RunPython(move_keys_to_accounts, restore_department_keys),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="department", name="apipay_api_key_encrypted"),
                migrations.RemoveField(model_name="department", name="apipay_webhook_secret_encrypted"),
                migrations.RemoveField(model_name="department", name="apipay_updated_at"),
            ],
            database_operations=[],
        ),
    ]
