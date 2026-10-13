"""Подключения ApiPay: хранение ключей, API суперюзера и общий кабинет на несколько отделов."""
import pytest

from apps.sales.models import ApiPayAccount, Department

pytestmark = pytest.mark.django_db

URL = "/api/apipay-accounts/"


def _account(name="Мельница", key="live-key-1234", secret="hook"):
    account = ApiPayAccount(name=name)
    account.set_api_key(key)
    account.set_webhook_secret(secret)
    account.save()
    return account


def test_account_stores_credentials_encrypted():
    account = _account()

    stored = ApiPayAccount.objects.get(pk=account.pk)
    assert stored.api_key == "live-key-1234"
    assert stored.webhook_secret == "hook"
    assert "live-key-1234" not in stored.api_key_encrypted
    assert "hook" not in stored.webhook_secret_encrypted


def test_unreadable_key_counts_as_not_configured(settings):
    department = Department.objects.create(code="mill", name="Мельница")
    department.apipay_account = _account()
    department.save()
    settings.SECRET_KEY = "rotated-without-fallback" * 3
    settings.SECRET_KEY_FALLBACKS = []

    stored = Department.objects.select_related("apipay_account").get(pk=department.pk)
    # Нечитаемый ключ равен отсутствующему: отдел показывается без Kaspi,
    # и суперюзер вводит ключ заново.
    assert stored.apipay_account.api_key == ""
    assert not stored.apipay_configured


def test_one_account_serves_several_departments(admin_user, auth_client):
    mill = Department.objects.create(code="mill", name="Мельница")
    city = Department.objects.create(code="city", name="Нью-Сити")
    api = auth_client(admin_user)

    created = api.post(
        URL,
        {
            "name": " Kaspi  Мельница ",
            "api_key": " live-key-1234 ",
            "webhook_secret": "hook",
            "departments": [mill.pk, city.pk],
        },
        format="json",
    )

    assert created.status_code == 201, created.data
    assert created.data["name"] == "Kaspi Мельница"
    assert created.data["key_hint"] == "••••1234"
    assert created.data["webhook_configured"] is True
    assert sorted(created.data["departments"]) == sorted([mill.pk, city.pk])
    assert "live-key-1234" not in str(created.data)
    assert "api_key" not in created.data and "webhook_secret" not in created.data
    rows = {row["code"]: row for row in api.get("/api/departments/").data}
    assert rows["mill"]["apipay_configured"] is True
    assert rows["city"]["apipay_account_name"] == "Kaspi Мельница"


def test_departments_move_between_accounts(admin_user, auth_client):
    mill = Department.objects.create(code="mill", name="Мельница")
    city = Department.objects.create(code="city", name="Нью-Сити")
    first = _account("Первый", "first-key-1234")
    second = _account("Второй", "second-key-5678")
    Department.objects.filter(pk__in=[mill.pk, city.pk]).update(apipay_account=first)
    api = auth_client(admin_user)

    moved = api.patch(f"{URL}{second.pk}/", {"departments": [city.pk]}, format="json")
    assert moved.status_code == 200, moved.data
    assert moved.data["departments"] == [city.pk]
    city.refresh_from_db()
    assert city.apipay_account_id == second.pk

    # Снятый с подключения отдел остаётся без Kaspi.
    dropped = api.patch(f"{URL}{first.pk}/", {"departments": []}, format="json")
    assert dropped.status_code == 200
    mill.refresh_from_db()
    assert mill.apipay_account_id is None
    assert not mill.apipay_configured


def test_key_and_secret_change_separately(admin_user, auth_client):
    account = _account()
    api = auth_client(admin_user)

    renamed = api.patch(f"{URL}{account.pk}/", {"name": "Касса"}, format="json")
    assert renamed.status_code == 200
    account.refresh_from_db()
    assert (account.name, account.api_key, account.webhook_secret) == (
        "Касса", "live-key-1234", "hook",
    )

    api.patch(f"{URL}{account.pk}/", {"webhook_secret": "hook-2"}, format="json")
    api.patch(f"{URL}{account.pk}/", {"api_key": "next-key-9999"}, format="json")
    account.refresh_from_db()
    assert (account.api_key, account.webhook_secret) == ("next-key-9999", "hook-2")


def test_invalid_and_duplicate_keys_are_rejected(admin_user, auth_client):
    _account("Мельница", "live-key-1234")
    api = auth_client(admin_user)

    no_key = api.post(URL, {"name": "Без ключа"}, format="json")
    short = api.post(URL, {"name": "Короткий", "api_key": "abc"}, format="json")
    spaced = api.post(URL, {"name": "Пробел", "api_key": "live key 1234"}, format="json")
    duplicate = api.post(URL, {"name": "Копия", "api_key": "live-key-1234"}, format="json")
    same_name = api.post(URL, {"name": "мельница", "api_key": "other-key-1234"}, format="json")

    for response in (no_key, short, spaced, duplicate):
        assert response.status_code == 400
        assert "api_key" in response.data["detail"]
    assert "«Мельница»" in str(duplicate.data["detail"]["api_key"])
    assert same_name.status_code == 400 and "name" in same_name.data["detail"]
    assert ApiPayAccount.objects.count() == 1


def test_delete_leaves_departments_without_kaspi(admin_user, auth_client):
    mill = Department.objects.create(code="mill", name="Мельница")
    mill.apipay_account = _account()
    mill.save()

    response = auth_client(admin_user).delete(f"{URL}{mill.apipay_account_id}/")

    assert response.status_code == 204
    mill.refresh_from_db()
    assert mill.apipay_account_id is None


def test_only_superuser_manages_accounts(user_with_perms, manager, auth_client):
    admin = user_with_perms("dept-admin", codes=["sys_permissions.manage"])
    account = _account()
    mill = Department.objects.create(code="mill", name="Мельница", apipay_account=account)

    for user in (admin, manager):
        api = auth_client(user)
        assert api.get(URL).status_code == 403
        assert api.post(URL, {"name": "X", "api_key": "x-key-12345"}, format="json").status_code == 403
        assert api.patch(f"{URL}{account.pk}/", {"departments": []}, format="json").status_code == 403

    # Сотрудники видят только факт подключения и его название, без ключа.
    rows = auth_client(manager).get("/api/departments/").data
    row = next(row for row in rows if row["id"] == mill.pk)
    assert row["apipay_configured"] is True
    assert row["apipay_account_name"] == "Мельница"
    assert "live-key-1234" not in str(rows) and "••••" not in str(rows)
    # Отдел не переносится к подключению через свою карточку.
    other = _account("Другой", "other-key-5678")
    auth_client(admin).patch(
        f"/api/departments/{mill.pk}/", {"apipay_account": other.pk}, format="json"
    )
    mill.refresh_from_db()
    assert mill.apipay_account_id == account.pk
