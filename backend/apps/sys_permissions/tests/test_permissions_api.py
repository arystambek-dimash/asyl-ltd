import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, admin_user):
    return auth_client(admin_user)


def test_permissions_list(admin_client):
    response = admin_client.get("/api/permissions/")
    assert response.status_code == 200
    assert any(item["code"] == "orders.create" for item in response.data)
    permission = next(
        item for item in response.data if item["code"] == "monoblock.view"
    )
    assert {key: permission[key] for key in ("code", "section", "action", "label")} == {
        "code": "monoblock.view",
        "section": "monoblock",
        "action": "view",
        "label": "Моноблок: Доступ (видит всё)",
    }


def test_permissions_list_follows_the_catalog_order_with_section_labels(admin_client):
    """Пикер прав показывает разделы в порядке меню и подписывает их как страницы."""
    from apps.sys_permissions.perms import PERMISSIONS

    response = admin_client.get("/api/permissions/")
    catalog = [permission["code"] for permission in PERMISSIONS]
    listed = [item["code"] for item in response.data if item["code"] in catalog]
    assert listed == catalog
    labels = {item["section"]: item["section_label"] for item in response.data}
    assert labels["stores"] == "Магазины"
    assert labels["bots"] == "WhatsApp-бот"


def test_permissions_list_puts_codes_outside_the_catalog_last(admin_client):
    from apps.sys_permissions.models import Permission

    Permission.objects.create(code="aaa.legacy", section="aaa", action="legacy", label="Старое")
    response = admin_client.get("/api/permissions/")
    assert response.data[-1]["code"] == "aaa.legacy"
    assert response.data[-1]["section_label"] == "aaa"


def test_permissions_list_requires_catalog_or_employee_management(
    auth_client,
    user_with_perms,
):
    employee_viewer = user_with_perms(
        "employee-viewer", codes=["employees.view"]
    )
    assert auth_client(employee_viewer).get("/api/permissions/").status_code == 403

    catalog_viewer = user_with_perms(
        "permission-viewer", codes=["sys_permissions.manage"]
    )
    assert auth_client(catalog_viewer).get("/api/permissions/").status_code == 200

    employee_manager = user_with_perms(
        "employee-manager-catalog", codes=["employees.manage"]
    )
    assert auth_client(employee_manager).get("/api/permissions/").status_code == 200


def test_roles_endpoint_is_removed(admin_client):
    assert admin_client.get("/api/roles/").status_code == 404
