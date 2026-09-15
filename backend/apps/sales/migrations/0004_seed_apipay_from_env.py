"""Разовый перенос общего ключа ApiPay из окружения в основной отдел.

Раньше ключ и секрет вебхука жили в ``.env`` (APIPAY_API_KEY,
APIPAY_WEBHOOK_SECRET). Теперь у каждого отдела свой ключ, и на первом деплое
старый ключ достаётся основному отделу, чтобы оплаты не остановились до
того, как суперюзер откроет настройку отделов. Если у какого-то отдела ключ
уже есть, окружение игнорируется. Обратная миграция ничего не трогает.
"""
import os

from django.db import migrations

from apps.common.crypto import encrypt_secret


def seed_from_environment(Department) -> bool:
    api_key = os.environ.get("APIPAY_API_KEY", "").strip()
    if not api_key:
        return False
    if Department.objects.exclude(apipay_api_key_encrypted="").exists():
        return False
    target = (
        Department.objects.filter(is_active=True, is_default=True)
        .order_by("created_at", "id")
        .first()
        or Department.objects.filter(is_active=True)
        .order_by("created_at", "id")
        .first()
    )
    if target is None:
        return False
    from django.utils import timezone

    webhook_secret = os.environ.get("APIPAY_WEBHOOK_SECRET", "").strip()
    Department.objects.filter(pk=target.pk).update(
        apipay_api_key_encrypted=encrypt_secret(api_key),
        apipay_webhook_secret_encrypted=encrypt_secret(webhook_secret),
        apipay_updated_at=timezone.now(),
    )
    return True


def seed(apps, schema_editor):
    seed_from_environment(apps.get_model("sales", "Department"))


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0003_department_apipay_credentials"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
