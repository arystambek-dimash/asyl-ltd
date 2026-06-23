import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db, django_user_model):
    def _make(username="u", password="pass12345", client=False, groups=()):
        from django.contrib.auth.models import Group
        user = django_user_model.objects.create_user(
            username=username, password=password, is_client=client
        )
        for g in groups:
            grp, _ = Group.objects.get_or_create(name=g)
            user.groups.add(grp)
        return user
    return _make


@pytest.fixture
def user_with_perms(make_user):
    from rbac.models import Permission, Role
    from employees.models import Employee

    def _make(username="emp", codes=()):
        user = make_user(username=username)
        role = Role.objects.create(name=f"role-{username}")
        for c in codes:
            p, _ = Permission.objects.get_or_create(
                code=c,
                defaults={"section": c.split(".")[0], "action": c.split(".")[1], "label": c})
            role.permissions.add(p)
        Employee.objects.create(user=user, first_name="A", last_name="B", phone="x", role=role)
        return user
    return _make


@pytest.fixture
def manager(user_with_perms):
    return user_with_perms("manager", codes=[
        "catalog.view", "catalog.create", "catalog.edit", "catalog.delete",
        "clients.view", "clients.create", "clients.edit", "clients.delete",
        "orders.view", "orders.create", "orders.edit", "orders.confirm"])


@pytest.fixture
def accountant(user_with_perms):
    return user_with_perms("accountant", codes=["payments.view", "payments.create",
                                                "payments.confirm", "orders.view"])


@pytest.fixture
def operator(user_with_perms):
    return user_with_perms("operator", codes=[
        "shipping.view", "shipping.arrive", "shipping.load", "shipping.ship",
        "orders.view", "warehouse.view", "events.view"])


@pytest.fixture
def boss(user_with_perms):
    return user_with_perms("boss", codes=[
        "shipping.view", "shipping.arrive", "shipping.load", "shipping.ship",
        "shipping.debt_override", "orders.view", "warehouse.view", "warehouse.adjust",
        "catalog.view", "clients.view"])


@pytest.fixture
def client_user(make_user):
    return make_user(username="client", client=True)


@pytest.fixture
def auth_client():
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    def _auth(user):
        c = APIClient()
        token = RefreshToken.for_user(user).access_token
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return c
    return _auth
