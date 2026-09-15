"""Ключ ApiPay и секрет вебхука у отдела: хранение и API."""
import importlib

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


def test_superuser_sets_key_and_secret_without_echo(superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    response = _api(superuser).patch(
        f"/api/departments/{department.id}/",
        {"apipay_api_key": " live-key-1234 ", "apipay_webhook_secret": "hook"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["apipay_configured"] is True
    assert response.data["apipay_webhook_configured"] is True
    assert response.data["apipay_key_hint"] == "••••1234"
    assert response.data["apipay_updated_at"]
    assert "apipay_api_key" not in response.data
    assert "apipay_webhook_secret" not in response.data
    assert "live-key-1234" not in str(response.data)
    department.refresh_from_db()
    assert department.apipay_api_key == "live-key-1234"
    assert department.apipay_webhook_secret == "hook"


def test_manager_with_manage_permission_cannot_touch_keys(user_with_perms):
    manager = user_with_perms("dept-admin", codes=["sys_permissions.manage"])
    department = Department.objects.create(code="mill", name="Мельница")
    response = _api(manager).patch(
        f"/api/departments/{department.id}/",
        {"name": "Мельница 2", "apipay_api_key": "live-key-1234"},
        format="json",
    )
    assert response.status_code == 403
    assert response.data["code"] == "apipay_superuser_only"
    department.refresh_from_db()
    assert department.name == "Мельница"
    assert not department.apipay_configured
    # Обычное редактирование того же менеджера по-прежнему работает.
    ok = _api(manager).patch(
        f"/api/departments/{department.id}/", {"name": "Мельница 2"}, format="json"
    )
    assert ok.status_code == 200
    assert "apipay_key_hint" not in ok.data
    assert ok.data["apipay_configured"] is False


def test_clearing_key_drops_secret_and_secret_needs_key(superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    api = _api(superuser)
    url = f"/api/departments/{department.id}/"

    bad = api.patch(url, {"apipay_webhook_secret": "hook"}, format="json")
    assert bad.status_code == 400
    assert bad.data["code"] == "apipay_secret_without_key"

    short = api.patch(url, {"apipay_api_key": "abc"}, format="json")
    assert short.status_code == 400

    api.patch(
        url,
        {"apipay_api_key": "live-key-1234", "apipay_webhook_secret": "hook"},
        format="json",
    )
    # Секрет можно сменить отдельно, когда ключ уже есть.
    only_secret = api.patch(url, {"apipay_webhook_secret": "hook-2"}, format="json")
    assert only_secret.status_code == 200
    department.refresh_from_db()
    assert department.apipay_api_key == "live-key-1234"
    assert department.apipay_webhook_secret == "hook-2"

    cleared = api.patch(url, {"apipay_api_key": ""}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["apipay_configured"] is False
    assert cleared.data["apipay_webhook_configured"] is False
    assert cleared.data["apipay_key_hint"] == ""
    department.refresh_from_db()
    assert department.apipay_webhook_secret == ""


def test_staff_list_shows_status_but_hint_only_to_superuser(manager, superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    department.set_apipay_api_key("live-key-1234")
    department.save()

    rows = _api(manager).get("/api/departments/").data
    mill = next(row for row in rows if row["code"] == "mill")
    assert mill["apipay_configured"] is True
    assert mill["apipay_webhook_configured"] is False
    assert "apipay_key_hint" not in mill
    assert "live-key-1234" not in str(rows)

    rows = _api(superuser).get("/api/departments/").data
    mill = next(row for row in rows if row["code"] == "mill")
    assert mill["apipay_key_hint"] == "••••1234"
    assert "live-key-1234" not in str(rows)


def test_create_applies_keys_only_for_superuser(superuser, user_with_perms):
    manager = user_with_perms("dept-admin", codes=["sys_permissions.manage"])
    denied = _api(manager).post(
        "/api/departments/",
        {"name": "Новый", "color": "#315FD5", "apipay_api_key": "live-key-1234"},
        format="json",
    )
    assert denied.status_code == 403
    created = _api(superuser).post(
        "/api/departments/",
        {"name": "Новый", "color": "#315FD5", "apipay_api_key": "live-key-1234"},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["apipay_configured"] is True


def test_seed_from_env_fills_default_department_once(monkeypatch):
    seed = importlib.import_module("apps.sales.migrations.0004_seed_apipay_from_env")
    Department.objects.all().delete()
    main = Department.objects.create(code="mill", name="Мельница", is_default=True)
    other = Department.objects.create(code="city", name="Нью-Сити")

    monkeypatch.delenv("APIPAY_API_KEY", raising=False)
    assert seed.seed_from_environment(Department) is False

    monkeypatch.setenv("APIPAY_API_KEY", "env-key-1234")
    monkeypatch.setenv("APIPAY_WEBHOOK_SECRET", "env-hook")
    assert seed.seed_from_environment(Department) is True
    main.refresh_from_db()
    other.refresh_from_db()
    assert main.apipay_api_key == "env-key-1234"
    assert main.apipay_webhook_secret == "env-hook"
    assert not other.apipay_configured

    # Повторный запуск (или другой ключ в env) ничего не перезаписывает.
    monkeypatch.setenv("APIPAY_API_KEY", "other-key-5678")
    assert seed.seed_from_environment(Department) is False
    main.refresh_from_db()
    assert main.apipay_api_key == "env-key-1234"
