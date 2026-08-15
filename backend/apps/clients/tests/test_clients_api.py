import pytest

from apps.clients.models import Client
from apps.clients.views import ClientViewSet
from apps.eventlog.models import EventLog
from apps.sales.models import Department

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


def test_manager_assigns_and_returns_client_department(auth_client, manager):
    department = Department.objects.create(
        code="client-team",
        name="Отдел по работе с клиентами",
    )

    created = auth_client(manager).post(
        "/api/clients/",
        {
            "first_name": "Отдел",
            "phone": "1",
            "department": department.pk,
        },
        format="json",
    )

    assert created.status_code == 201
    assert created.data["department"] == department.pk
    assert created.data["department_name"] == department.name
    client = Client.objects.get(pk=created.data["id"])
    assert client.department_id == department.pk

    cleared = auth_client(manager).patch(
        f"/api/clients/{client.pk}/",
        {"department": None},
        format="json",
    )

    assert cleared.status_code == 200
    assert cleared.data["department"] is None
    assert cleared.data["department_name"] is None


def test_inactive_department_cannot_be_newly_assigned_to_client(
    auth_client,
    manager,
):
    department = Department.objects.create(
        code="inactive-client-team",
        name="Архивный отдел",
        is_active=False,
    )

    rejected = auth_client(manager).post(
        "/api/clients/",
        {
            "first_name": "Архив",
            "phone": "1",
            "department": department.pk,
        },
        format="json",
    )

    assert rejected.status_code == 400
    assert "department" in rejected.data["detail"]


def test_client_can_keep_its_existing_inactive_department(auth_client, manager):
    department = Department.objects.create(
        code="archived-existing-client-team",
        name="Старый отдел",
    )
    client = Client.objects.create_with_user(
        first_name="Старый",
        phone="1",
        department=department,
    )
    department.is_active = False
    department.save(update_fields=["is_active"])

    response = auth_client(manager).patch(
        f"/api/clients/{client.pk}/",
        {"phone": "2", "department": department.pk},
        format="json",
    )

    assert response.status_code == 200
    client.refresh_from_db()
    assert client.phone == "2"
    assert client.department_id == department.pk


def test_client_department_change_is_audited(auth_client, manager):
    first = Department.objects.create(code="audit-first", name="Первый")
    second = Department.objects.create(code="audit-second", name="Второй")
    client = Client.objects.create_with_user(
        first_name="Аудит",
        phone="1",
        department=first,
    )

    response = auth_client(manager).patch(
        f"/api/clients/{client.pk}/",
        {"department": second.pk},
        format="json",
    )

    assert response.status_code == 200
    event = EventLog.objects.get(
        event_type="client",
        payload__action="client_department_changed",
    )
    assert event.payload == {
        "client_id": client.pk,
        "action": "client_department_changed",
        "department_from": first.code,
        "department_to": second.code,
    }


def test_client_department_change_rejects_open_ai_session(
    auth_client, manager,
):
    from apps.cameras.models import AiCountingSession
    from apps.orders.models import Order

    first = Department.objects.create(code="active-first", name="Первый")
    second = Department.objects.create(code="active-second", name="Второй")
    client = Client.objects.create_with_user(
        first_name="Активный",
        phone="active-client-department",
        department=first,
    )
    order = Order.objects.create(
        client=client,
        status="loading",
        loading_camera="cam2",
    )
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=manager,
    )

    response = auth_client(manager).patch(
        f"/api/clients/{client.pk}/",
        {"department": second.pk},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "active_loading"
    client.refresh_from_db()
    session.refresh_from_db()
    assert client.department_id == first.pk
    assert session.status == AiCountingSession.ACTIVE


def test_stale_update_cannot_resurrect_deleted_client(
    auth_client,
    manager,
    monkeypatch,
):
    client = Client.objects.create_with_user(
        first_name="Удалённый",
        phone="stale-update-original",
    )
    stale_client = Client.objects.select_related("user").get(pk=client.pk)
    client_pk = client.pk
    Client.objects.filter(pk=client_pk).delete()
    monkeypatch.setattr(
        ClientViewSet,
        "get_object",
        lambda _view: stale_client,
    )

    response = auth_client(manager).patch(
        f"/api/clients/{client_pk}/",
        {"phone": "stale-update-new"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "client_not_active"
    assert not Client.objects.filter(pk=client_pk).exists()


def test_stale_update_rechecks_client_department_after_lock(
    auth_client,
    user_with_perms,
    monkeypatch,
):
    first = Department.objects.create(code="stale-client-a", name="Отдел A")
    second = Department.objects.create(code="stale-client-b", name="Отдел B")
    editor = user_with_perms(
        "stale-client-editor",
        codes=["clients.view", "clients.edit"],
    )
    editor.employee.sales_department = first
    editor.employee.save(update_fields=["sales_department"])
    client = Client.objects.create_with_user(
        first_name="Перенесённый",
        phone="stale-client-original",
        department=first,
    )
    stale_client = Client.objects.select_related("user").get(pk=client.pk)
    client.department = second
    client.save(update_fields=["department"])
    monkeypatch.setattr(ClientViewSet, "get_object", lambda _view: stale_client)

    response = auth_client(editor).patch(
        f"/api/clients/{client.pk}/",
        {"phone": "stale-client-new"},
        format="json",
    )

    assert response.status_code == 403
    client.refresh_from_db()
    assert client.phone == "stale-client-original"
    assert client.department_id == second.pk


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


def test_stale_password_request_cannot_reactivate_deleted_client_account(
    auth_client,
    portal_access_manager,
    monkeypatch,
):
    client = Client.objects.create_with_user(
        first_name="Удалённый",
        phone="stale-password-client",
    )
    stale_client = Client.objects.select_related("user").get(pk=client.pk)
    portal_user = stale_client.user
    original_hash = portal_user.password
    client_pk = client.pk
    Client.objects.filter(pk=client_pk).delete()
    monkeypatch.setattr(
        ClientViewSet,
        "get_object",
        lambda _view: stale_client,
    )

    response = auth_client(portal_access_manager).post(
        f"/api/clients/{client_pk}/password/",
        {"password": "Temporary-client-pass-2026!"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "client_not_active"
    assert not Client.objects.filter(pk=client_pk).exists()
    portal_user.refresh_from_db()
    assert portal_user.password == original_hash
    assert portal_user.is_active is False


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
