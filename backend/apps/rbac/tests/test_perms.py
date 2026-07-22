from apps.rbac.perms import ALL_CODES, PRESETS


def test_payments_confirm_exists():
    assert "payments.confirm" in ALL_CODES


def test_payments_confirm_in_presets():
    for role in ("Касса", "Менеджер", "Начальник"):
        assert "payments.confirm" in PRESETS[role]


def test_statement_export_in_financial_presets():
    for role in ("Касса", "Менеджер", "Начальник"):
        assert "reports.export" in PRESETS[role]


def test_cashier_permission_and_role_removed():
    assert "payments.cashier" not in ALL_CODES
    assert "Кассир" not in PRESETS
