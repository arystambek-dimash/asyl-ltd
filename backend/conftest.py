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
def manager(make_user):
    return make_user(username="manager", groups=("manager",))


@pytest.fixture
def accountant(make_user):
    return make_user(username="accountant", groups=("accountant",))


@pytest.fixture
def operator(make_user):
    return make_user(username="operator", groups=("operator",))


@pytest.fixture
def boss(make_user):
    return make_user(username="boss", groups=("boss",))


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
