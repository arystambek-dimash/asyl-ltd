"""End-to-end health monitoring for the factory camera path.

The probe deliberately exercises two independent views of the system:

* RTSP DESCRIBE against every expected MediaMTX path, which catches NVR,
  Tailscale and MediaMTX failures and gives a per-camera count;
* a JPEG frame through go2rtc, which proves the same route used by the browser
  can actually start a producer and deliver video bytes.

Results and the monitor heartbeat are durable in PostgreSQL.  Transient full
outages are debounced before an incident is opened; recovery is debounced too.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from . import ai, alerts, services
from .models import CameraHealthState, CameraIncident

log = logging.getLogger(__name__)


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except (TypeError, ValueError):
        log.error("Invalid %s; using %s", name, default)
        return default


# This repository serves one fixed factory site. Environment overrides may
# raise these protection floors for future expansion, but a typo must never
# redefine a ten-camera outage as a healthy 1/1 installation.
SITE_EXPECTED_COUNT_FLOOR = 10
SITE_MINIMUM_ONLINE_FLOOR = 5
EXPECTED_COUNT = max(
    SITE_EXPECTED_COUNT_FLOOR,
    _positive_int("CAMERA_EXPECTED_COUNT", SITE_EXPECTED_COUNT_FLOOR),
)
MINIMUM_ONLINE_COUNT = max(
    SITE_MINIMUM_ONLINE_FLOOR,
    _positive_int(
        "CAMERA_MINIMUM_ONLINE_COUNT", max(1, (EXPECTED_COUNT + 1) // 2)
    ),
)
FAILURE_THRESHOLD = _positive_int("CAMERA_FAILURE_THRESHOLD", 3)
DEGRADED_THRESHOLD = _positive_int("CAMERA_DEGRADED_THRESHOLD", 3)
RECOVERY_THRESHOLD = _positive_int("CAMERA_RECOVERY_THRESHOLD", 2)
STALE_SECONDS = _positive_int("CAMERA_HEALTH_STALE_SECONDS", 180)
INVENTORY_STALE_SECONDS = _positive_int("CAMERA_INVENTORY_STALE_SECONDS", 900)
ALERT_RETRY_SECONDS = _positive_int("CAMERA_ALERT_RETRY_SECONDS", 900)
GO2RTC_TIMEOUT_SECONDS = _positive_int("CAMERA_GO2RTC_TIMEOUT_SECONDS", 15)
FRAME_PROBE_COUNT = _positive_int("CAMERA_FRAME_PROBE_COUNT", 2)
FRAME_ROTATION_SECONDS = _positive_int("CAMERA_FRAME_ROTATION_SECONDS", 30)
FRAME_RESULT_TTL_SECONDS = _positive_int("CAMERA_FRAME_RESULT_TTL_SECONDS", 600)
SITE_TIMEZONE = os.environ.get("CAMERA_SITE_TIMEZONE") or "Asia/Almaty"


def expected_streams() -> tuple[str, ...]:
    default_streams = tuple(f"cam{number}" for number in range(1, EXPECTED_COUNT + 1))
    raw_configured = [
        value.strip()
        for value in (os.environ.get("CAMERA_EXPECTED_STREAMS") or "").split(",")
        if value.strip()
    ]
    # Preserve order while preventing a typo from probing the same source many
    # times. A malformed override must never lower the fixed site's protected
    # baseline: otherwise ``CAMERA_EXPECTED_STREAMS=cam1`` could turn a
    # nine-camera loss into a green 1/1 result.
    configured = tuple(
        dict.fromkeys(name for name in raw_configured if ai.CAM_RE.fullmatch(name))
    )
    if raw_configured and len(configured) < EXPECTED_COUNT:
        log.critical(
            "Ignoring CAMERA_EXPECTED_STREAMS below protected baseline (%s/%s)",
            len(configured),
            EXPECTED_COUNT,
        )
        return default_streams
    return configured or default_streams


@dataclass(frozen=True)
class Observation:
    status: str
    expected_count: int
    online_count: int
    components: dict
    streams: dict
    error: str = ""

    def details(self) -> dict:
        return {
            "status": self.status,
            "expected_count": self.expected_count,
            "online_count": self.online_count,
            "components": self.components,
            "streams": self.streams,
            "error": self.error,
        }


def _rtsp_path(stream: str) -> str:
    # The UI stream camN is backed by the low-bandwidth camNsub path in
    # go2rtc.yaml. Direct cameras already use stable cam_<mac> names and don't
    # have the numeric NVR suffix convention.
    return f"{stream}sub" if stream[3:].isdigit() else stream


def _probe_rtsp(stream: str) -> tuple[str, str]:
    return stream, services._probe_path(_rtsp_path(stream))


def _inventory_component(now: datetime) -> dict:
    if not ai.enabled():
        return {"configured": False, "reachable": None, "fresh": None}
    try:
        inventory = ai.inventory()
        devices = inventory.get("devices") or []
        relevant = [d for d in devices if d.get("kind") in ("nvr-channel", "direct")]
        result = {
            "configured": True,
            "reachable": True,
            "devices": len(relevant),
            "online": sum(bool(d.get("online", True)) for d in relevant),
            "fresh": None,
        }
        updated_text = str(inventory.get("updated") or "").strip()
        if updated_text:
            result["updated"] = updated_text
            try:
                updated = datetime.fromisoformat(updated_text)
                if timezone.is_naive(updated):
                    updated = updated.replace(tzinfo=ZoneInfo(SITE_TIMEZONE))
                age = max(0, int((now - updated).total_seconds()))
                result.update(age_seconds=age, fresh=age <= INVENTORY_STALE_SECONDS)
            except (ValueError, TypeError, KeyError):
                result["fresh"] = None
        return result
    except (ai.AiUnavailable, ai.AiError) as exc:
        return {
            "configured": True,
            "reachable": False,
            "fresh": False,
            "error": type(exc).__name__,
        }


def _go2rtc_catalog() -> tuple[dict, set[str]]:
    if not services.GO2RTC_API:
        return {"reachable": False, "error": "not configured"}, set()
    request = urllib.request.Request(f"{services.GO2RTC_API}/api/streams", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read(2_000_000) or b"{}")
        names = set(payload) if isinstance(payload, dict) else set()
        return {"reachable": True, "configured_streams": len(names)}, names
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        return {"reachable": False, "error": type(exc).__name__}, set()


def _go2rtc_frame(stream: str) -> tuple[bool, str]:
    query = urllib.parse.urlencode({"src": stream})
    request = urllib.request.Request(
        f"{services.GO2RTC_API}/api/frame.jpeg?{query}", method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=GO2RTC_TIMEOUT_SECONDS) as response:
            prefix = response.read(3)
            content_type = response.headers.get_content_type()
        if response.status == 200 and prefix == b"\xff\xd8\xff":
            return True, ""
        return False, f"invalid frame ({response.status}, {content_type})"
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return False, type(exc).__name__


def probe_once(now: datetime | None = None) -> Observation:
    """Perform a cache-free end-to-end probe without raising network errors."""

    now = now or timezone.now()
    streams = expected_streams()
    if not streams:
        return Observation(
            status=CameraHealthState.OUTAGE,
            expected_count=0,
            online_count=0,
            components={"configuration": {"valid": False}},
            streams={},
            error="no valid expected streams configured",
        )

    statuses: dict[str, str] = {}
    # Inventory, go2rtc API and all RTSP paths are independent and therefore
    # run together. Worst-case wall time stays near one network timeout.
    with ThreadPoolExecutor(max_workers=min(20, len(streams) + 2)) as pool:
        inventory_future = pool.submit(_inventory_component, now)
        go2rtc_future = pool.submit(_go2rtc_catalog)
        rtsp_futures = [pool.submit(_probe_rtsp, stream) for stream in streams]
        for future in as_completed(rtsp_futures):
            try:
                stream, status = future.result()
            except Exception:  # a broken single probe must not kill heartbeat
                log.exception("Unexpected RTSP camera probe error")
                continue
            statuses[stream] = status
        try:
            inventory = inventory_future.result()
        except Exception as exc:
            log.exception("Unexpected camera inventory probe error")
            inventory = {"configured": True, "reachable": False, "error": type(exc).__name__}
        try:
            go2rtc, go2rtc_names = go2rtc_future.result()
        except Exception as exc:
            log.exception("Unexpected go2rtc catalog probe error")
            go2rtc, go2rtc_names = {"reachable": False, "error": type(exc).__name__}, set()

    rtsp_online = [stream for stream in streams if statuses.get(stream) == "online"]
    minimum_online = min(MINIMUM_ONLINE_COUNT, len(streams))
    configured = [stream for stream in streams if stream in go2rtc_names]
    go2rtc["expected_configured"] = len(configured)

    # A catalog entry proves only configuration. Rotate a small number of real
    # JPEG probes per interval and persist their latest outcomes. This catches
    # every frozen browser path without spawning ten simultaneous decoders on
    # the small production VPS.
    frame_results: dict[str, tuple[bool, str]] = {}
    frame_candidates = [stream for stream in rtsp_online if stream in go2rtc_names]
    prior_frame_health: dict = {}
    try:
        prior_state = CameraHealthState.objects.only("components").first()
        prior_frame_health = (
            (prior_state.components or {}).get("go2rtc", {}).get("frame_health", {})
            if prior_state
            else {}
        )
    except Exception:
        # Probe still works during first migration/bootstrap; persistence is an
        # enhancement, never a reason to lose the heartbeat.
        prior_frame_health = {}

    selected_frames: list[str] = []
    if frame_candidates:
        count = min(FRAME_PROBE_COUNT, len(frame_candidates))
        cycle = int(now.timestamp()) // FRAME_ROTATION_SECONDS
        offset = (cycle * count) % len(frame_candidates)
        selected_frames = [
            frame_candidates[(offset + index) % len(frame_candidates)]
            for index in range(count)
        ]

    if go2rtc.get("reachable") and selected_frames:
        with ThreadPoolExecutor(max_workers=min(2, len(selected_frames))) as pool:
            futures = {
                pool.submit(_go2rtc_frame, stream): stream for stream in selected_frames
            }
            for future in as_completed(futures):
                stream = futures[future]
                try:
                    frame_results[stream] = future.result()
                except Exception as exc:
                    log.exception("Unexpected go2rtc frame probe error for %s", stream)
                    frame_results[stream] = (False, type(exc).__name__)

    frame_health: dict[str, dict] = {}
    for stream in frame_candidates:
        if stream in frame_results:
            ok, error = frame_results[stream]
            frame_health[stream] = {
                "ok": ok,
                "checked_at": now.isoformat(),
                "error": error[:120],
            }
            continue
        prior = prior_frame_health.get(stream)
        if not isinstance(prior, dict):
            continue
        try:
            checked_at = datetime.fromisoformat(str(prior.get("checked_at") or ""))
            if timezone.is_naive(checked_at):
                checked_at = checked_at.replace(tzinfo=ZoneInfo("UTC"))
            if (now - checked_at).total_seconds() <= FRAME_RESULT_TTL_SECONDS:
                frame_health[stream] = {
                    "ok": bool(prior.get("ok")),
                    "checked_at": checked_at.isoformat(),
                    "error": str(prior.get("error") or "")[:120],
                }
        except (TypeError, ValueError):
            pass

    frame_failures = [
        stream for stream in frame_candidates if frame_health.get(stream, {}).get("ok") is False
    ]
    usable = (
        [stream for stream in frame_candidates if stream not in frame_failures]
        if go2rtc.get("reachable")
        else []
    )
    catalog_missing = [stream for stream in streams if stream not in go2rtc_names]
    go2rtc.update(
        frame=bool(usable),
        frame_online=len(usable),
        frame_expected=len(streams),
        frame_checked=selected_frames,
        frame_failures=frame_failures,
        frame_health=frame_health,
        catalog_missing=catalog_missing,
    )

    stream_results = dict(statuses)
    for stream in catalog_missing:
        if stream_results.get(stream) == "online":
            stream_results[stream] = "go2rtc-missing"
    for stream in frame_failures:
        stream_results[stream] = "no-frame"

    online_count = len(usable)
    errors: list[str] = []
    if online_count < minimum_online:
        errors.append(
            f"critical usable video capacity loss ({online_count}/{len(streams)}, "
            f"minimum {minimum_online})"
        )
    if not go2rtc.get("reachable"):
        errors.append("go2rtc API unavailable")
    elif len(configured) < minimum_online:
        errors.append(
            f"critical go2rtc catalog loss ({len(configured)}/{len(streams)})"
        )

    if errors:
        status = CameraHealthState.OUTAGE
    elif online_count < len(streams):
        status = CameraHealthState.DEGRADED
    elif inventory.get("configured") and (
        inventory.get("reachable") is False or inventory.get("fresh") is False
    ):
        status = CameraHealthState.DEGRADED
    else:
        status = CameraHealthState.HEALTHY

    return Observation(
        status=status,
        expected_count=len(streams),
        online_count=online_count,
        components={
            "mediamtx": {
                "reachable": bool(rtsp_online),
                "online": len(rtsp_online),
                "expected": len(streams),
                "minimum_online": minimum_online,
            },
            "go2rtc": go2rtc,
            "inventory": inventory,
        },
        streams={stream: stream_results.get(stream, "error") for stream in streams},
        error="; ".join(errors),
    )


def _open_incident() -> CameraIncident | None:
    return (
        CameraIncident.objects.select_for_update()
        .filter(resolved_at__isnull=True)
        .first()
    )


def _record_degraded_incident(
    state: CameraHealthState, observation: Observation, now: datetime
) -> tuple[CameraIncident, bool]:
    """Create/update a partial-loss incident; never downgrade an outage."""

    incident = _open_incident()
    created = incident is None
    if incident is None:
        incident = CameraIncident.objects.create(
            severity=CameraIncident.DEGRADED,
            started_at=state.first_degraded_at or now,
            confirmed_at=now,
            expected_count=observation.expected_count,
            minimum_online_count=observation.online_count,
            degraded_details=observation.details(),
        )
    elif incident.severity == CameraIncident.OUTAGE:
        # A partial recovery from an outage is not a new degraded transition.
        # Keep the critical incident open until full recovery and preserve its
        # alert ordering instead of emitting a warning before a throttled
        # outage notification.
        return incident, False
    else:
        incident.minimum_online_count = min(
            incident.minimum_online_count, observation.online_count
        )
        incident.degraded_details = observation.details()
        incident.save(update_fields=["minimum_online_count", "degraded_details"])
    return incident, created


def _record_outage_incident(
    state: CameraHealthState, observation: Observation, now: datetime
) -> tuple[CameraIncident, bool]:
    """Create an outage incident or promote the current degraded incident."""

    incident = _open_incident()
    transitioned = incident is None or incident.severity != CameraIncident.OUTAGE
    if incident is None:
        incident = CameraIncident.objects.create(
            severity=CameraIncident.OUTAGE,
            started_at=state.first_failure_at or now,
            confirmed_at=now,
            expected_count=observation.expected_count,
            minimum_online_count=observation.online_count,
            outage_details=observation.details(),
        )
    else:
        update_fields = ["severity", "minimum_online_count", "outage_details"]
        if (
            incident.degraded_details
            and incident.degraded_alert_sent_at is None
            and incident.degraded_alert_superseded_at is None
        ):
            # Escalation supersedes an undelivered warning: the critical alert
            # must not wait behind a retry-throttled degraded notification.
            incident.degraded_alert_superseded_at = now
            update_fields.append("degraded_alert_superseded_at")
        incident.severity = CameraIncident.OUTAGE
        incident.minimum_online_count = min(
            incident.minimum_online_count, observation.online_count
        )
        incident.outage_details = observation.details()
        incident.save(update_fields=update_fields)
    return incident, transitioned


@transaction.atomic
def record_observation(
    observation: Observation, now: datetime | None = None
) -> tuple[CameraHealthState, int | None]:
    """Debounce and persist an observation; return transition incident id."""

    now = now or timezone.now()
    state, _ = CameraHealthState.objects.select_for_update().get_or_create(singleton=True)
    previous = state.status
    transition_incident_id: int | None = None

    state.observed_status = observation.status
    state.expected_count = observation.expected_count
    state.online_count = observation.online_count
    state.components = observation.components
    state.streams = observation.streams
    state.last_error = observation.error[:1000]
    state.last_checked_at = now

    if observation.status == CameraHealthState.OUTAGE:
        state.degraded_streak = 0
        state.recovery_streak = 0
        state.failure_streak += 1
        if state.first_failure_at is None:
            state.first_failure_at = now
        if previous != CameraHealthState.OUTAGE and state.failure_streak >= FAILURE_THRESHOLD:
            state.status = CameraHealthState.OUTAGE
            state.outage_started_at = state.first_failure_at
            state.last_changed_at = now
            incident, transitioned = _record_outage_incident(
                state, observation, now
            )
            if transitioned:
                transition_incident_id = incident.pk
        elif previous == CameraHealthState.OUTAGE:
            _record_outage_incident(state, observation, now)
    elif observation.status == CameraHealthState.DEGRADED:
        state.last_good_at = now
        state.failure_streak = 0
        state.first_failure_at = None
        state.degraded_streak += 1
        if state.first_degraded_at is None:
            state.first_degraded_at = now
        if previous == CameraHealthState.OUTAGE:
            state.recovery_streak += 1
            if state.recovery_streak >= RECOVERY_THRESHOLD:
                state.status = CameraHealthState.DEGRADED
                state.recovery_streak = 0
                state.outage_started_at = None
                state.last_changed_at = now
        else:
            state.recovery_streak = 0
            state.status = CameraHealthState.DEGRADED
            if previous != state.status:
                state.last_changed_at = now

        open_incident = _open_incident()
        if open_incident is not None or state.degraded_streak >= DEGRADED_THRESHOLD:
            incident, created = _record_degraded_incident(state, observation, now)
            if created:
                transition_incident_id = incident.pk
    else:
        # Full recovery is also debounced when an incident is open. This keeps
        # a one-good-frame blip from closing an alert that is still active.
        state.last_good_at = now
        state.failure_streak = 0
        state.first_failure_at = None
        state.degraded_streak = 0
        state.first_degraded_at = None
        open_incident = _open_incident()
        if previous == CameraHealthState.OUTAGE or open_incident is not None:
            state.recovery_streak += 1
            if state.recovery_streak >= RECOVERY_THRESHOLD:
                state.status = CameraHealthState.HEALTHY
                state.recovery_streak = 0
                state.outage_started_at = None
                state.last_changed_at = now
                if open_incident:
                    open_incident.resolved_at = now
                    open_incident.recovery_details = observation.details()
                    open_incident.save(
                        update_fields=["resolved_at", "recovery_details"]
                    )
                    transition_incident_id = open_incident.pk
        else:
            state.recovery_streak = 0
            state.status = CameraHealthState.HEALTHY
            if previous != state.status:
                state.last_changed_at = now

    state.save()
    return state, transition_incident_id


def _alert_payload(incident: CameraIncident, event: str) -> dict:
    if event == "camera_recovery":
        details = incident.recovery_details
    elif event == "camera_degraded":
        details = incident.degraded_details
    else:
        details = incident.outage_details
    online = details.get("online_count", 0)
    expected = details.get("expected_count", incident.expected_count)
    if event == "camera_recovery":
        duration = max(0, int(((incident.resolved_at or timezone.now()) - incident.started_at).total_seconds()))
        message = (
            f"КАМЕРЫ ВОССТАНОВЛЕНЫ: доступно {online}/{expected}. "
            f"Длительность инцидента: {duration} сек."
        )
        severity = "resolved"
    elif event == "camera_degraded":
        message = (
            f"СНИЖЕНИЕ ДОСТУПНОСТИ КАМЕР: доступно {online}/{expected}. "
            "Рабочие камеры не перезапускаются; требуется проверить отсутствующие каналы."
        )
        severity = "warning"
    else:
        message = (
            f"КРИТИЧЕСКИЙ СБОЙ КАМЕР: доступно {online}/{expected}. "
            "Монитор подтвердил недоступность видеотракта."
        )
        severity = "critical"
    return {
        "message": message,
        "severity": severity,
        "incident_id": incident.pk,
        "started_at": incident.started_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "online_count": online,
        "expected_count": expected,
        "components": details.get("components", {}),
    }


def _deliver_incident(incident: CameraIncident, event: str, now: datetime) -> None:
    fields = {
        "camera_degraded": ("degraded_alert_attempted_at", "degraded_alert_sent_at"),
        "camera_outage": ("outage_alert_attempted_at", "outage_alert_sent_at"),
        "camera_recovery": ("recovery_alert_attempted_at", "recovery_alert_sent_at"),
    }
    attempted_field, sent_field = fields[event]

    setattr(incident, attempted_field, now)
    incident.save(update_fields=[attempted_field])
    delivery = alerts.send(event, _alert_payload(incident, event))
    updates = ["alert_error"]
    incident.alert_error = "; ".join(delivery.errors)[:1000]
    if delivery.delivered:
        setattr(incident, sent_field, now)
        updates.append(sent_field)
    incident.save(update_fields=updates)


def deliver_pending_alerts(now: datetime | None = None) -> None:
    """Deliver or periodically retry every durable incident transition.

    Transition alerts remain pending after recovery. This matters when the
    destination itself is temporarily unavailable: losing the outage and later
    sending only a recovery would give operators a dangerously incomplete
    history.
    """

    now = now or timezone.now()
    retry_before = now - timedelta(seconds=ALERT_RETRY_SECONDS)

    degraded_candidates = (
        CameraIncident.objects.filter(
            degraded_alert_sent_at__isnull=True,
            degraded_alert_superseded_at__isnull=True,
        )
        .exclude(degraded_details={})
        .order_by("confirmed_at")[:20]
    )
    for incident in degraded_candidates:
        if (
            incident.degraded_alert_attempted_at is None
            or incident.degraded_alert_attempted_at <= retry_before
        ):
            _deliver_incident(incident, event="camera_degraded", now=now)

    outage_candidates = (
        CameraIncident.objects.filter(outage_alert_sent_at__isnull=True)
        .exclude(outage_details={})
        .order_by("confirmed_at")[:20]
    )
    for incident in outage_candidates:
        if (
            incident.outage_alert_attempted_at is None
            or incident.outage_alert_attempted_at <= retry_before
        ):
            _deliver_incident(incident, event="camera_outage", now=now)

    recoveries = CameraIncident.objects.filter(
        resolved_at__isnull=False, recovery_alert_sent_at__isnull=True
    ).order_by("resolved_at")[:20]
    for incident in recoveries:
        # Keep externally visible event order intact. A recovery is sent only
        # after every transition that actually occurred has been delivered.
        transitions_delivered = (
            (
                not incident.degraded_details
                or incident.degraded_alert_sent_at is not None
                or incident.degraded_alert_superseded_at is not None
            )
            and (not incident.outage_details or incident.outage_alert_sent_at is not None)
        )
        if (
            transitions_delivered
            and (
                incident.recovery_alert_attempted_at is None
                or incident.recovery_alert_attempted_at <= retry_before
            )
        ):
            _deliver_incident(incident, event="camera_recovery", now=now)


def monitor_once(now: datetime | None = None) -> CameraHealthState:
    now = now or timezone.now()
    observation = probe_once(now=now)
    state, _ = record_observation(observation, now=now)
    deliver_pending_alerts(now=now)
    return state


def state_payload(
    state: CameraHealthState | None = None,
    *,
    now: datetime | None = None,
    max_age: int | None = None,
    required_since: datetime | None = None,
) -> dict:
    """Serializable state with stale-heartbeat detection for API/deploy gates."""

    now = now or timezone.now()
    max_age = max_age or STALE_SECONDS
    state = state or CameraHealthState.objects.first()
    if state is None:
        return {
            "status": "unavailable",
            "recorded_status": None,
            "stale": True,
            "detail": "camera monitor has not reported yet",
        }
    age = (
        max(0, int((now - state.last_checked_at).total_seconds()))
        if state.last_checked_at
        else None
    )
    before_required_start = bool(
        required_since
        and (state.last_checked_at is None or state.last_checked_at < required_since)
    )
    stale = age is None or age > max_age or before_required_start
    confirming_outage = (
        state.observed_status == CameraHealthState.OUTAGE
        and state.status != CameraHealthState.OUTAGE
    )
    effective_status = "unavailable" if stale else state.status
    incident = CameraIncident.objects.filter(resolved_at__isnull=True).first()
    return {
        "status": effective_status,
        "recorded_status": state.status,
        "observed_status": state.observed_status,
        "stale": stale,
        "fresh_since_required_start": not before_required_start,
        "confirming_outage": confirming_outage,
        "age_seconds": age,
        "expected_count": state.expected_count,
        "online_count": state.online_count,
        "failure_streak": state.failure_streak,
        "degraded_streak": state.degraded_streak,
        "recovery_streak": state.recovery_streak,
        "last_checked_at": state.last_checked_at,
        "last_good_at": state.last_good_at,
        "outage_started_at": state.outage_started_at,
        "components": state.components,
        "streams": state.streams,
        "detail": state.last_error,
        "incident_id": incident.pk if incident else None,
    }


def exit_code(payload: dict, *, fail_on_degraded: bool = False) -> int:
    if (
        payload.get("stale")
        or payload.get("confirming_outage")
        or payload.get("status") in ("unavailable", "initializing")
    ):
        return 2
    if payload.get("status") == CameraHealthState.OUTAGE:
        return 3
    if fail_on_degraded and payload.get("status") == CameraHealthState.DEGRADED:
        return 4
    return 0
