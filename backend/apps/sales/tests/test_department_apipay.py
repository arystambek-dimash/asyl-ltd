"""Ключ ApiPay и секрет вебхука у отдела: хранение и API."""
import pytest
from rest_framework.test import APIClient

from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def superuser(make_user):
    user = make_user("root")
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


def test_department_stores_apipay_credentials_encrypted():
    department = Department.objects.create(code="mill", name="Мельница")
    assert not department.apipay_configured
    assert department.apipay_api_key == ""

    department.set_apipay_api_key("live-key-1234")
    department.set_apipay_webhook_secret("hook-secret")
    department.save()

    stored = Department.objects.get(pk=department.pk)
    assert stored.apipay_configured
    assert stored.apipay_api_key == "live-key-1234"
    assert stored.apipay_webhook_secret == "hook-secret"
    assert "live-key-1234" not in stored.apipay_api_key_encrypted
    assert "hook-secret" not in stored.apipay_webhook_secret_encrypted
    assert stored.apipay_updated_at is not None

    stored.set_apipay_api_key("")
    stored.save()
    assert not stored.apipay_configured
    assert stored.apipay_webhook_secret == ""


def test_unreadable_token_counts_as_not_configured(settings):
    department = Department.objects.create(code="mill", name="Мельница")
    department.set_apipay_api_key("live-key-1234")
    department.save()
    settings.SECRET_KEY = "rotated-without-fallback" * 3
    settings.SECRET_KEY_FALLBACKS = []
    stored = Department.objects.get(pk=department.pk)
    assert stored.apipay_api_key == ""
    # Нечитаемый ключ равен отсутствующему: отдел показывается как
    # «не подключён», и суперюзер вводит ключ заново.
    assert not stored.apipay_configured
