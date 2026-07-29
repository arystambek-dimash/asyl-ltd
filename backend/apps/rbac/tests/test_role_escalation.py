"""Управление ролями не должно быть лазейкой к чужим правам.

Права роли входят в effective_perm_codes сотрудника, поэтому правка
собственной роли действует немедленно. Без проверок сотрудник с одним
лишь rbac.manage выписывал себе подтверждение оплат и отгрузку в долг.
"""
import pytest

from apps.employees.models import Employee
from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


def _perm(code: str) -> Permission:
    section, action = code.split(".")
    perm, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action, "label": code},
    )
    return perm


def _role_manager(make_user, auth_client, codes=("rbac.view", "rbac.manage")):
    """Сотрудник, управляющий ролями: права выданы через саму роль."""
    user = make_user(username="role-admin")
    role = Role.objects.create(name="Управляющий ролями")
    role.permissions.set([_perm(c) for c in codes])
    Employee.objects.create(
        user=user, first_name="A", last_name="B", phone="x", role=role)
    return user, role, auth_client(user)


def test_cannot_grant_permissions_the_actor_lacks(make_user, auth_client):
    """Нельзя выдать роли право, которого нет у самого выдающего.

    Проверяем на чужой роли: свою правку отсекает отдельная защита.
    """
    _user, _role, client = _role_manager(make_user, auth_client)
    _perm("payments.confirm")
    _perm("shipping.debt_override")
    other = Role.objects.create(name="Подставная роль")

    response = client.patch(
        f"/api/roles/{other.id}/",
        {"permission_codes": ["payments.confirm", "shipping.debt_override"]},
        format="json",
    )

    assert response.status_code == 400
    assert response.json().get("code") == "perm_escalation"
    assert not other.permissions.exists()


def test_cannot_create_role_with_permissions_the_actor_lacks(make_user, auth_client):
    """Тот же запрет на создании — иначе обходится новой ролью."""
    _user, _role, client = _role_manager(make_user, auth_client)
    _perm("payments.confirm")

    response = client.post(
        "/api/roles/",
        {"name": "Обходная роль", "permission_codes": ["payments.confirm"]},
        format="json",
    )

    assert response.status_code == 400
    assert response.json().get("code") == "perm_escalation"
    assert not Role.objects.filter(name="Обходная роль").exists()


def test_cannot_edit_own_role(make_user, auth_client):
    """Свою роль править нельзя даже в пределах уже имеющихся прав."""
    user, role, client = _role_manager(make_user, auth_client)

    response = client.patch(
        f"/api/roles/{role.id}/", {"name": "Переименовал себе"}, format="json",
    )

    assert response.status_code == 400
    assert response.json().get("code") == "self_role_edit"


def test_can_grant_permissions_the_actor_holds(make_user, auth_client):
    """Раздача прав в пределах своих — обычная работа, она не ломается."""
    user, _role, client = _role_manager(
        make_user, auth_client, codes=("rbac.view", "rbac.manage", "orders.view"))
    other = Role.objects.create(name="Другая роль")

    response = client.patch(
        f"/api/roles/{other.id}/",
        {"permission_codes": ["orders.view"]},
        format="json",
    )

    assert response.status_code == 200
    assert set(other.permissions.values_list("code", flat=True)) == {"orders.view"}


def test_superuser_is_not_restricted(make_user, auth_client):
    """Суперадмин раздаёт любые права — он и так имеет все."""
    root = make_user(username="root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])
    role = Role.objects.create(name="Касса-тест")
    _perm("payments.confirm")

    response = auth_client(root).patch(
        f"/api/roles/{role.id}/",
        {"permission_codes": ["payments.confirm"]},
        format="json",
    )

    assert response.status_code == 200


def test_cannot_delete_own_role(make_user, auth_client):
    """Свою роль не удаляют: удаляющий сам остался бы без прав."""
    _user, role, client = _role_manager(make_user, auth_client)

    response = client.delete(f"/api/roles/{role.id}/")

    assert response.status_code == 400
    assert response.json().get("code") == "self_role_edit"
    assert Role.objects.filter(pk=role.pk).exists()
