from django.conf import settings

from config.celery import app


def test_celery_uses_json_utc_and_no_result_backend() -> None:
    assert settings.CELERY_RESULT_BACKEND is None
    assert settings.CELERY_TASK_IGNORE_RESULT is True
    assert settings.CELERY_ACCEPT_CONTENT == ["json"]
    assert settings.CELERY_TASK_SERIALIZER == "json"
    assert settings.CELERY_RESULT_SERIALIZER == "json"
    assert settings.CELERY_EVENT_SERIALIZER == "json"
    assert settings.CELERY_ENABLE_UTC is True
    assert settings.CELERY_TIMEZONE == settings.TIME_ZONE

    # Exercise the standard config/celery.py namespaced settings bridge, not
    # just the raw Django constants above.
    assert app.conf.result_backend is None
    assert app.conf.task_ignore_result is True
    assert app.conf.accept_content == ["json"]
    assert app.conf.enable_utc is True
    assert app.conf.timezone == "Asia/Almaty"


def test_apipay_schedule_routes_only_to_expiring_payments_queue() -> None:
    app.autodiscover_tasks(force=True)
    route = settings.CELERY_TASK_ROUTES["orders.reconcile_apipay"]
    scheduled = settings.CELERY_BEAT_SCHEDULE["reconcile-apipay"]

    assert "orders.reconcile_apipay" in app.tasks
    assert route == {"queue": "payments"}
    assert scheduled["task"] == "orders.reconcile_apipay"
    assert scheduled["options"]["queue"] == "payments"
    assert 0 < scheduled["options"]["expires"] < scheduled["schedule"]
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS == {
        "visibility_timeout": 1200,
    }
    assert settings.CELERY_BEAT_SYNC_EVERY == 1
