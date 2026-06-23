from apps.rbac.perms import ALL_CODES, PRESETS


def test_payments_confirm_exists():
    assert "payments.confirm" in ALL_CODES


def test_payments_confirm_in_presets():
    for role in ("Бухгалтер", "Менеджер", "Начальник"):
        assert "payments.confirm" in PRESETS[role]
