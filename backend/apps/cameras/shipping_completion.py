"""Evidence gates for automatic loading completion; no hardware or DB writes."""

from datetime import timedelta
import math

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import ai, shipping_tracking

QUIET_SECONDS = 40
MAX_SAMPLE_GAP = timedelta(seconds=15)
CARGO_MAX_AGE = timedelta(seconds=5)


def _time(raw):
    try:
        value = parse_datetime(raw) if isinstance(raw, str) else None
        return value if value and timezone.is_aware(value) else None
    except (TypeError, ValueError):
        return None


def status(phase="idle", detail="", *, now=None, **internal):
    return {
        "state": phase,
        "remaining_seconds": None,
        "observed_at": (now or timezone.now()).isoformat(),
        "detail": detail,
        **internal,
    }


def public_status(raw, *, historical=False):
    if not isinstance(raw, dict) or not raw:
        return None
    value = {key: raw.get(key) for key in (
        "state", "remaining_seconds", "observed_at", "detail",
    )}
    if not historical and value["state"] in ("waiting", "finishing"):
        observed = _time(value["observed_at"])
        if observed is None or not timezone.now() - CARGO_MAX_AGE <= observed <= timezone.now() + timedelta(seconds=5):
            return status("blocked", "Нет свежих данных для автоматического завершения")
    return value


def cargo_observation(live, session_id):
    """Only inference evidence from this exact continuous loading can prove idle."""
    if not isinstance(live, dict):
        return None
    if not (
        live.get("running") is True
        and live.get("mode") == "session"
        and live.get("continuous_analytics") is True
        and live.get("analytics_scope") == "shipping"
    ):
        return None
    try:
        ai.assert_order_session_identity(live, session_id)
    except (ai.AiError, ai.AiUnavailable):
        return None
    value = live.get("cargo_activity")
    if not isinstance(value, dict):
        return None
    observed = _time(value.get("observed_at"))
    clear_since = _time(value.get("clear_since"))
    last_activity = _time(value.get("last_activity_at"))
    now = timezone.now()
    if (
        type(value.get("schema_version")) is not int or value["schema_version"] != 1
        or value.get("basis") != "bag_detections_and_scene_motion"
        or value.get("state") not in ("clear", "active", "unknown")
        or not isinstance(value.get("generation"), str)
        or not 1 <= len(value["generation"]) <= 256
        or type(value.get("sequence")) is not int or value["sequence"] < 0
        or observed is None or not now - CARGO_MAX_AGE <= observed <= now + timedelta(seconds=5)
        or not isinstance(value.get("reason"), str)
        or (value.get("last_activity_at") is not None and last_activity is None)
        or (last_activity is not None and last_activity > observed)
        or (value["state"] == "clear" and (
            clear_since is None or clear_since > observed
            or (last_activity is not None and last_activity > clear_since)
        ))
        or (value["state"] != "clear" and value.get("clear_since") is not None)
    ):
        return None
    return value


def evaluate(previous, tracking, live, session_id):
    """Require a continuous locally observed quiet interval, reset on uncertainty.

    CV's older clear/absence timestamps alone never credit downtime or a worker
    restart. Both cameras must keep supplying new evidence throughout the timer.
    """
    now = timezone.now()
    body = shipping_tracking.current_tracking(tracking)
    if body["presence"] == "present":
        return status("idle", "Транспорт в зоне — погрузка активна", now=now)
    if body["presence"] != "absent":
        return status("blocked", "Присутствие транспорта неизвестно; автоматическое завершение приостановлено", now=now)
    cargo = cargo_observation(live, session_id)
    if cargo is None or cargo["state"] == "unknown":
        return status("blocked", "Нет свежей проверки конвейера; погрузка сохранена", now=now)
    if cargo["state"] == "active":
        return status("blocked", "На конвейере есть мешки или движение; ожидаем окончания подачи", now=now)

    body_at, cargo_at = _time(body["observed_at"]), _time(cargo["observed_at"])
    sample_at = min(body_at, cargo_at)
    previous = previous if isinstance(previous, dict) else {}
    last_sample = _time(previous.get("observed_at"))
    previous_body_at = _time(previous.get("transport_observed_at"))
    previous_cargo_at = _time(previous.get("cargo_observed_at"))
    since = _time(previous.get("since"))
    same_window = bool(
        previous.get("state") == "waiting"
        and previous.get("session_id") == session_id
        and previous.get("activity_generation") == cargo["generation"]
        and previous.get("transport_absent_since") == body["absent_since"]
        and previous.get("transport_visit_id") == body["visit_id"]
        and previous.get("cargo_clear_since") == cargo["clear_since"]
        and last_sample and timedelta(0) < sample_at - last_sample <= MAX_SAMPLE_GAP
        and previous_body_at and body_at > previous_body_at
        and previous_cargo_at and cargo_at > previous_cargo_at
        and type(previous.get("activity_sequence")) is int
        and cargo["sequence"] > previous["activity_sequence"]
        and since and since <= sample_at
    )
    # First confirmed pair begins the local interval. Old CV timestamps cannot
    # instantly close an order after monitor startup or recovery from an error.
    since = since if same_window else max(sample_at, now)
    elapsed = max(0, (sample_at - since).total_seconds())
    remaining = max(0, math.ceil(QUIET_SECONDS - elapsed))
    # CV independently confirms a full clear interval, including weak boxes,
    # generic motion and fresh inference frames before its atomic final freeze.
    if cargo_at - _time(cargo["clear_since"]) < timedelta(seconds=QUIET_SECONDS):
        remaining = max(remaining, math.ceil(QUIET_SECONDS - (cargo_at - _time(cargo["clear_since"])).total_seconds()))
    return status(
        "waiting", "Транспорт отсутствует, конвейер свободен. Ожидаем 40 секунд перед завершением",
        now=sample_at, remaining_seconds=remaining, since=since.isoformat(),
        session_id=session_id,
        transport_observed_at=body["observed_at"],
        transport_absent_since=body["absent_since"],
        transport_visit_id=body["visit_id"],
        cargo_observed_at=cargo["observed_at"],
        cargo_clear_since=cargo["clear_since"],
        activity_generation=cargo["generation"], activity_sequence=cargo["sequence"],
    )


def ready(value):
    return value.get("state") == "waiting" and value.get("remaining_seconds") == 0


def guard(value, *, recovery_only=False):
    return {
        "schema_version": 1,
        "activity_generation": value["activity_generation"],
        "min_clear_seconds": QUIET_SECONDS,
        **({"recovery_only": True} if recovery_only else {}),
    }
