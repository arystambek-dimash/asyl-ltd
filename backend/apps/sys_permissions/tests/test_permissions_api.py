import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    user = make_user(username="permissions-root")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return auth_client(user)


def test_permissions_list(admin_client):
    response = admin_client.get("/api/permissions/")
    assert response.status_code == 200
    assert any(item["code"] == "orders.create" for item in response.data)
    permission = next(
        item for item in response.data if item["code"] == "ai_247.manage"
    )
    assert {key: permission[key] for key in ("code", "section", "action", "label")} == {
        "code": "ai_247.manage",
        "section": "ai_247",
        "action": "manage",
        "label": "AI 24/7: Управление",
    }


def test_permissions_list_requires_catalog_or_employee_management(
    auth_client,
    user_with_perms,
):
    employee_viewer = user_with_perms(
        "employee-viewer", codes=["employees.view"]
    )
    assert auth_client(employee_viewer).get("/api/permissions/").status_code == 403

    catalog_viewer = user_with_perms(
        "permission-viewer", codes=["sys_permissions.view"]
    )
    assert auth_client(catalog_viewer).get("/api/permissions/").status_code == 200

    employee_manager = user_with_perms(
        "employee-manager-catalog", codes=["employees.manage"]
    )
    assert auth_client(employee_manager).get("/api/permissions/").status_code == 200


def test_roles_endpoint_is_removed(admin_client):
    assert admin_client.get("/api/roles/").status_code == 404
