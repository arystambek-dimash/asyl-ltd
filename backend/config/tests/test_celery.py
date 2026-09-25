from unittest.mock import patch

from django.conf import settings

from apps.orders.reconciliation_runner import ApiPayReconciliationOptions
from config.apipay_reconciliation import reconcile_timing, reconcile_timing_from_env
from config.celery import app


def test_celery_uses_json_utc_and_no_result_backend() -> None:
    assert settings.CELERY_RESULT_BACKEND is None
    assert settings.CELERY_TASK_IGNORE_RESULT is True
    assert settings.CELERY_ACCEPT_CONTENT == ["json"]
    assert settings.CELERY_TASK_SERIALIZER == "json"
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


def test_broker_visibility_and_beat_follow_the_task_lease() -> None:
    # Settings и задача берут интервал и lease из одной формулы: задача,
    # упавшая без ack, не должна вернуться из брокера раньше, чем истечёт lease.
    options = ApiPayReconciliationOptions.from_environment()
    scheduled = settings.CELERY_BEAT_SCHEDULE["reconcile-apipay"]

    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] == (
        options.task_lock_seconds
    )
    assert scheduled["schedule"] == options.interval_seconds


def test_reconcile_timing_clamps_interval_backoff_and_lease() -> None:
    timing = reconcile_timing(
        interval_seconds=5, max_backoff_seconds=10, task_lock_seconds=0
    )
    assert (
        timing.interval_seconds,
        timing.max_backoff_seconds,
        timing.task_lock_seconds,
    ) == (15, 15, 90)

    with patch.dict(
        "os.environ",
        {
            "APIPAY_RECONCILE_INTERVAL_SECONDS": "oops",
            "APIPAY_MONITOR_MAX_BACKOFF_SECONDS": "900",
            "APIPAY_RECONCILE_TASK_LOCK_SECONDS": "",
        },
    ):
        timing = reconcile_timing_from_env()
    assert (
        timing.interval_seconds,
        timing.max_backoff_seconds,
        timing.task_lock_seconds,
    ) == (30, 900, 1200)


def test_beat_entries_reach_the_queue_their_task_is_routed_to() -> None:
    # Beat passes entry options straight into apply_async, and an explicit
    # queue there overrides CELERY_TASK_ROUTES — the orientation export used
    # to land on the single payments worker this way.
    for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
        route = settings.CELERY_TASK_ROUTES.get(entry["task"])
        if route is None:
            continue
        options = app.amqp.router.route(dict(entry.get("options", {})), entry["task"])
        assert options["queue"].name == route["queue"], name
