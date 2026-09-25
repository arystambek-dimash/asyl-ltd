import pytest

pytestmark = pytest.mark.django_db


def test_superuser_has_any_code(admin_user):
    assert admin_user.has_perm_code("orders.create") is True


def test_employee_permissions_grant_codes(user_with_perms):
    user = user_with_perms("employee-codes", codes=["orders.view"])

    assert user.has_perm_code("orders.view") is True
    assert user.has_perm_code("orders.create") is False
    assert user.perm_codes == {"orders.view"}


def test_employee_without_direct_permissions_has_no_codes(user_with_perms):
    user = user_with_perms("employee-no-codes")
    assert user.perm_codes == set()


def test_user_without_employee_has_no_codes(make_user):
    user = make_user(username="user-no-employee")
    assert user.perm_codes == set()
    assert user.has_perm_code("orders.view") is False
