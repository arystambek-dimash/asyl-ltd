from apps.sys_permissions.perms import ALL_CODES, PERMISSIONS

RETIRED_CODES = {
    "shipping.view", "shipping.load", "shipping.ship", "shipping.arrive",
    "shipping.rollback", "shipping.debt_override", "train.view", "train.load",
    "ai_247.manage", "sys_permissions.view", "catalog.delete",
    "grain.lab", "grain.dispatch", "grain.unload", "grain.exit",
    "rbac.view", "rbac.manage",
}


def test_codes_are_unique():
    codes = [permission["code"] for permission in PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_known_codes_are_present():
    expected = {
        "orders.create",
        "orders.rollback",
        "monoblock.view",
        "loader.confirm",
        "loader.trucks",
        "loader.wagons",
        "clients.set_price",
        "clients.manage_access",
        "reports.export",
        "employees.manage",
        "grain.delete",
        "grain.correct_weighing",
        "sys_permissions.manage",
    }
    assert expected <= ALL_CODES


def test_retired_codes_are_not_in_runtime_catalog():
    assert RETIRED_CODES.isdisjoint(ALL_CODES)
