import pytest

from apps.clients.models import Client
from apps.rbac.scoping import scope_by_department


pytestmark = pytest.mark.django_db


def test_empty_scope_permission_never_falls_back_to_all(user_with_perms):
    Client.objects.create(
        first_name="Main", last_name="Client", phone="1", department="main")
    Client.objects.create(
        first_name="Field", last_name="Client", phone="2", department="field")
    unscoped = user_with_perms("unscoped", codes=["warehouse.view"])

    visible = scope_by_department(Client.objects.all(), unscoped, "clients.view")

    assert list(visible) == []


def test_base_permission_grants_main_department_only(user_with_perms):
    main = Client.objects.create(
        first_name="Main", last_name="Client", phone="1", department="main")
    Client.objects.create(
        first_name="Field", last_name="Client", phone="2", department="field")
    reporter = user_with_perms("main-reporter", codes=["reports.view"])

    visible = scope_by_department(Client.objects.all(), reporter, "reports.view")

    assert list(visible) == [main]
