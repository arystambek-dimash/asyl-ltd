import pytest

from apps.clients.models import Client
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_access_manager(user_with_perms):
    return user_with_perms(
        "portal-access-manager",
        codes=["clients.view", "clients.manage_access"],
    )


def test_manager_creates_client_without_optional_fields(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Иван", "last_name": "Петров", "phone": "+998..."},
    )
    assert resp.status_code == 201
    c = Client.objects.select_related("user").get(user__first_name="Иван")
    assert c.name == "Иван Петров"
    assert c.country == "" and c.iin == "" and c.bank == "" and c.bank_account == ""
    assert c.user.is_client is True
    assert c.user.is_active is False
    assert c.user.has_usable_password() is False
    assert c.user.must_change_password is True
    assert resp.data["username"] == c.user.username
    assert resp.data["first_name"] == "Иван"
    assert resp.data["last_name"] == "Петров"
    assert resp.data["user"] == c.user_id
    assert resp.data["portal_access_enabled"] is False
    assert resp.data["password_change_required"] is True


def test_country_and_requisites_optional(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Эксп", "last_name": "Орт", "phone": "x",
         "country": "Узбекистан"},
    )
    assert resp.status_code == 201


def test_last_name_is_optional(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/", {"first_name": "Айжан", "phone": "+77001112233"}
    )
    assert resp.status_code == 201
    client = Client.objects.select_related("user").get(user__first_name="Айжан")
    assert client.user.last_name == ""
    assert client.name == "Айжан"


def test_accountant_cannot_create_client(auth_client, accountant):
    resp = auth_client(accountant).post(
        "/api/clients/", {"first_name": "X", "last_name": "Y", "phone": "z"}
    )
    assert resp.status_code == 403


def test_generated_client_usernames_are_unicode_and_collision_safe(
    auth_client,
    manager,
):
    api = auth_client(manager)
    payload = {"first_name": "Әлия", "last_name": "Серік", "phone": "+7"}

    first = api.post("/api/clients/", payload, format="json")
    second = api.post("/api/clients/", payload, format="json")

    assert first.status_code == second.status_code == 201
    assert first.data["username"] == "әлия-серік"
    assert second.data["username"] == "әлия-серік-2"


def test_manager_updates_client_names_on_user(auth_client, manager):
    created = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "До", "last_name": "Правки", "phone": "+7"},
        format="json",
    )

    response = auth_client(manager).patch(
        f"/api/clients/{created.data['id']}/",
        {"first_name": "После", "last_name": "Изменения"},
        format="json",
    )

    assert response.status_code == 200
    client = Client.objects.select_related("user").get(pk=created.data["id"])
    assert client.user.first_name == "После"
    assert client.user.last_name == "Изменения"
    assert client.name == "После Изменения"
    assert response.data["first_name"] == "После"
    assert response.data["last_name"] == "Изменения"


def test_manager_sets_temporary_client_password_without_logging_it(
    auth_client,
    manager,
    portal_access_manager,
):
    created = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Портал", "last_name": "Клиент", "phone": "+7"},
        format="json",
    )
    password = " Temporary-client-pass-2026! "

    response = auth_client(portal_access_manager).post(
        f"/api/clients/{created.data['id']}/password/",
        {"password": password},
        format="json",
    )

    assert response.status_code == 204
    client = Client.objects.select_related("user").get(pk=created.data["id"])
    assert client.user.is_active is True
    assert client.user.must_change_password is True
    assert client.user.check_password(password)
    event = EventLog.objects.filter(
        event_type="client_security",
        payload__client_id=client.pk,
    ).latest("id")
    assert event.payload["password_changed"] is True
    assert password not in event.message
    assert password not in str(event.payload)

    detail = auth_client(manager).get(f"/api/clients/{client.pk}/")
    assert detail.status_code == 200
    assert detail.data["portal_access_enabled"] is False
    assert detail.data["password_change_required"] is True

    client.user.must_change_password = False
    client.user.save(update_fields=["must_change_password"])
    ready = auth_client(manager).get(f"/api/clients/{client.pk}/")
    assert ready.data["portal_access_enabled"] is True
    assert ready.data["password_change_required"] is False


def test_temporary_password_rolls_back_if_security_audit_fails(
    auth_client,
    manager,
    portal_access_manager,
    monkeypatch,
):
    created = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Аудит", "last_name": "Пароля", "phone": "+7"},
        format="json",
    )
    client = Client.objects.select_related("user").get(pk=created.data["id"])
    original_hash = client.user.password

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.clients.views.log_event", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        auth_client(portal_access_manager).post(
            f"/api/clients/{client.pk}/password/",
            {"password": "Temporary-client-pass-2026!"},
            format="json",
        )

    client.user.refresh_from_db()
    assert client.user.password == original_hash
    assert client.user.is_active is False
    assert client.user.has_usable_password() is False


def test_client_editor_cannot_change_portal_password(auth_client, manager):
    created = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Нет", "last_name": "Доступа", "phone": "+7"},
        format="json",
    )

    response = auth_client(manager).post(
        f"/api/clients/{created.data['id']}/password/",
        {"password": "Temporary-client-pass-2026!"},
        format="json",
    )

    assert response.status_code == 403
    client = Client.objects.select_related("user").get(pk=created.data["id"])
    assert client.user.is_active is False
    assert client.user.has_usable_password() is False


def test_deleting_client_deactivates_portal_user(auth_client, manager):
    created = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Удалить", "last_name": "Клиента", "phone": "+7"},
        format="json",
    )
    client = Client.objects.select_related("user").get(pk=created.data["id"])
    user = client.user
    user.is_active = True
    user.save(update_fields=["is_active"])

    response = auth_client(manager).delete(f"/api/clients/{client.pk}/")

    assert response.status_code == 204
    user.refresh_from_db()
    assert user.is_active is False
