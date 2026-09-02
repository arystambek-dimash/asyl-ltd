from apps.cameras.management.commands.check_camera_health import human_diagnostics


def test_human_health_diagnostics_explain_event_sync_failure():
    payload = {
        "status": "healthy",
        "online_count": 10,
        "expected_count": 10,
        "age_seconds": 4,
        "stale": False,
        "fresh_since_required_start": True,
        "confirming_outage": False,
        "detail": "",
        "event_sync": {
            "blocking": True,
            "cameras": [
                {
                    "camera": "cam3",
                    "status": "error",
                    "last_event_id": 19,
                    "detail": "malformed event page",
                }
            ],
        },
    }

    diagnostic = human_diagnostics(payload)

    assert "статус=работает" in diagnostic
    assert "камеры=10/10" in diagnostic
    assert "События cam3: ошибка" in diagnostic
    assert "последнее событие=19" in diagnostic
    assert "malformed event page" in diagnostic


def test_human_health_diagnostics_localize_missing_release_heartbeat():
    diagnostic = human_diagnostics(
        {
            "status": "unavailable",
            "stale": True,
            "fresh_since_required_start": False,
        }
    )

    assert "статус=нет данных" in diagnostic
    assert "heartbeat от текущего релиза ещё не получен" in diagnostic
