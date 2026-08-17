from apps.sys_permissions.perms import ALL_CODES, PERMISSIONS


def test_codes_are_unique():
    codes = [permission["code"] for permission in PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_known_codes_are_present():
    expected = {
        "orders.create",
        "shipping.debt_override",
        "clients.set_price",
        "clients.manage_access",
        "ai_247.manage",
        "reports.export",
        "employees.manage",
        "grain.delete",
        "sys_permissions.manage",
    }
    assert expected <= ALL_CODES


def test_legacy_rbac_codes_are_not_in_runtime_catalog():
    assert "rbac.view" not in ALL_CODES
    assert "rbac.manage" not in ALL_CODES
