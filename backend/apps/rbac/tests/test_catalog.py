from apps.rbac.perms import PERMISSIONS, ALL_CODES, PRESETS


def test_codes_unique():
    codes = [p["code"] for p in PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_presets_reference_existing_codes():
    for name, codes in PRESETS.items():
        for c in codes:
            assert c in ALL_CODES, f"{name}: unknown code {c}"


def test_known_codes_present():
    for c in ("orders.create", "shipping.debt_override", "clients.set_price",
              "employees.manage", "rbac.manage"):
        assert c in ALL_CODES
