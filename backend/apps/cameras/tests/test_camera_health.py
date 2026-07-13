from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.cameras import alerts, health
from apps.cameras.models import CameraHealthState, CameraIncident

pytestmark = pytest.mark.django_db


def observation(status, online, expected=10):
    return health.Observation(
        status=status,
        expected_count=expected,
        online_count=online,
        components={
            "mediamtx": {"reachable": online > 0},
            "go2rtc": {"reachable": status != CameraHealthState.OUTAGE, "frame": online > 0},
        },
        streams={f"cam{i}": "online" if i <= online else "offline" for i in range(1, expected + 1)},
        error="no video" if status == CameraHealthState.OUTAGE else "",
    )


def test_probe_healthy_exercises_rtsp_and_real_go2rtc_frame(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 3)
    monkeypatch.setattr(health, "FRAME_PROBE_COUNT", 3)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    monkeypatch.setattr(health, "_probe_rtsp", lambda stream: (stream, "online"))
    monkeypatch.setattr(
        health,
        "_inventory_component",
        lambda now: {"configured": True, "reachable": True, "fresh": True, "online": 3},
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True, "configured_streams": 3}, {"cam1", "cam2", "cam3"}),
    )
    frame = patch.object(health, "_go2rtc_frame", return_value=(True, ""))
    with frame as frame_probe:
        result = health.probe_once()

    assert result.status == CameraHealthState.HEALTHY
    assert result.online_count == 3
    assert result.components["go2rtc"]["frame"] is True
    assert {call.args[0] for call in frame_probe.call_args_list} == {"cam1", "cam2", "cam3"}


def test_expected_stream_override_cannot_reduce_protected_site_baseline(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 10)
    monkeypatch.setenv("CAMERA_EXPECTED_STREAMS", "cam1")

    assert health.expected_streams() == tuple(f"cam{i}" for i in range(1, 11))


def test_site_protection_floors_are_not_lower_than_production_baseline():
    assert health.EXPECTED_COUNT >= 10
    assert health.MINIMUM_ONLINE_COUNT >= 5


def test_probe_detects_one_missing_go2rtc_browser_stream(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 3)
    monkeypatch.setattr(health, "MINIMUM_ONLINE_COUNT", 2)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    monkeypatch.setattr(health, "_probe_rtsp", lambda stream: (stream, "online"))
    monkeypatch.setattr(
        health, "_inventory_component", lambda now: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True}, {"cam1", "cam2"}),
    )
    monkeypatch.setattr(health, "_go2rtc_frame", lambda stream: (True, ""))

    result = health.probe_once()
    assert result.status == CameraHealthState.DEGRADED
    assert result.online_count == 2
    assert result.streams["cam3"] == "go2rtc-missing"


def test_probe_detects_frozen_individual_go2rtc_stream(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 3)
    monkeypatch.setattr(health, "MINIMUM_ONLINE_COUNT", 2)
    monkeypatch.setattr(health, "FRAME_PROBE_COUNT", 3)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    monkeypatch.setattr(health, "_probe_rtsp", lambda stream: (stream, "online"))
    monkeypatch.setattr(
        health, "_inventory_component", lambda now: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True}, {"cam1", "cam2", "cam3"}),
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_frame",
        lambda stream: (False, "timeout") if stream == "cam2" else (True, ""),
    )

    result = health.probe_once()
    assert result.status == CameraHealthState.DEGRADED
    assert result.online_count == 2
    assert result.streams["cam2"] == "no-frame"


def test_rotating_frame_probe_keeps_recent_failure_until_recheck(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 3)
    monkeypatch.setattr(health, "MINIMUM_ONLINE_COUNT", 2)
    monkeypatch.setattr(health, "FRAME_PROBE_COUNT", 1)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    now = timezone.now()
    streams = ["cam1", "cam2", "cam3"]
    selected_index = (int(now.timestamp()) // health.FRAME_ROTATION_SECONDS) % 3
    failed = streams[(selected_index + 1) % 3]
    CameraHealthState.objects.create(
        components={
            "go2rtc": {
                "frame_health": {
                    failed: {"ok": False, "checked_at": now.isoformat(), "error": "timeout"}
                }
            }
        },
        last_checked_at=now,
    )
    monkeypatch.setattr(health, "_probe_rtsp", lambda stream: (stream, "online"))
    monkeypatch.setattr(
        health, "_inventory_component", lambda checked: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True}, set(streams)),
    )
    monkeypatch.setattr(health, "_go2rtc_frame", lambda stream: (True, ""))

    result = health.probe_once(now=now)
    assert result.status == CameraHealthState.DEGRADED
    assert result.online_count == 2
    assert result.streams[failed] == "no-frame"


def test_probe_one_camera_down_is_degraded_not_full_outage(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 3)
    monkeypatch.setattr(health, "MINIMUM_ONLINE_COUNT", 2)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)

    def rtsp(stream):
        return stream, "offline" if stream == "cam2" else "online"

    monkeypatch.setattr(health, "_probe_rtsp", rtsp)
    monkeypatch.setattr(
        health, "_inventory_component", lambda now: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True}, {"cam1", "cam2", "cam3"}),
    )
    monkeypatch.setattr(health, "_go2rtc_frame", lambda stream: (True, ""))

    result = health.probe_once()
    assert result.status == CameraHealthState.DEGRADED
    assert result.online_count == 2
    assert result.streams["cam2"] == "offline"


def test_probe_severe_partial_loss_is_treated_as_outage(monkeypatch):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 10)
    monkeypatch.setattr(health, "MINIMUM_ONLINE_COUNT", 5)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    monkeypatch.setattr(
        health,
        "_probe_rtsp",
        lambda stream: (stream, "online" if int(stream.removeprefix("cam")) <= 4 else "offline"),
    )
    monkeypatch.setattr(
        health, "_inventory_component", lambda now: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health,
        "_go2rtc_catalog",
        lambda: ({"reachable": True}, {f"cam{i}" for i in range(1, 11)}),
    )
    monkeypatch.setattr(health, "_go2rtc_frame", lambda stream: (True, ""))

    result = health.probe_once()
    assert result.online_count == 4
    assert result.status == CameraHealthState.OUTAGE
    assert "minimum 5" in result.error


@pytest.mark.parametrize("api_reachable,frame", [(False, False), (True, False)])
def test_probe_go2rtc_failure_is_full_outage(monkeypatch, api_reachable, frame):
    monkeypatch.setattr(health, "EXPECTED_COUNT", 2)
    monkeypatch.delenv("CAMERA_EXPECTED_STREAMS", raising=False)
    monkeypatch.setattr(health, "_probe_rtsp", lambda stream: (stream, "online"))
    monkeypatch.setattr(
        health, "_inventory_component", lambda now: {"configured": False, "reachable": None}
    )
    monkeypatch.setattr(
        health, "_go2rtc_catalog", lambda: ({"reachable": api_reachable}, {"cam1", "cam2"})
    )
    monkeypatch.setattr(health, "_go2rtc_frame", lambda stream: (frame, "failed"))

    result = health.probe_once()
    assert result.status == CameraHealthState.OUTAGE
    assert result.online_count == 0


def test_outage_and_recovery_are_debounced(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(health, "RECOVERY_THRESHOLD", 2)
    started = timezone.now()

    for offset in range(2):
        state, incident_id = health.record_observation(
            observation(CameraHealthState.OUTAGE, 0), started + timedelta(seconds=offset * 30)
        )
        assert state.status == CameraHealthState.INITIALIZING
        assert incident_id is None
    state, incident_id = health.record_observation(
        observation(CameraHealthState.OUTAGE, 0), started + timedelta(seconds=60)
    )
    assert state.status == CameraHealthState.OUTAGE
    assert incident_id is not None
    incident = CameraIncident.objects.get(pk=incident_id)
    assert incident.started_at == started
    assert incident.resolved_at is None

    state, _ = health.record_observation(
        observation(CameraHealthState.HEALTHY, 10), started + timedelta(seconds=90)
    )
    assert state.status == CameraHealthState.OUTAGE
    assert state.recovery_streak == 1

    state, recovered_incident_id = health.record_observation(
        observation(CameraHealthState.HEALTHY, 10), started + timedelta(seconds=120)
    )
    assert state.status == CameraHealthState.HEALTHY
    assert recovered_incident_id == incident_id
    incident.refresh_from_db()
    assert incident.resolved_at == started + timedelta(seconds=120)


def test_degraded_observation_never_opens_full_outage(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    state, incident_id = health.record_observation(
        observation(CameraHealthState.DEGRADED, 9)
    )
    assert state.status == CameraHealthState.DEGRADED
    assert incident_id is None
    assert not CameraIncident.objects.exists()


def test_persistent_single_camera_loss_opens_degraded_incident(monkeypatch):
    monkeypatch.setattr(health, "DEGRADED_THRESHOLD", 3)
    now = timezone.now()
    for offset in range(3):
        state, _ = health.record_observation(
            observation(CameraHealthState.DEGRADED, 9),
            now + timedelta(seconds=offset * 30),
        )
    incident = CameraIncident.objects.get()
    assert state.status == CameraHealthState.DEGRADED
    assert incident.severity == CameraIncident.DEGRADED
    assert incident.started_at == now
    assert incident.minimum_online_count == 9

    sender = patch.object(
        alerts, "send", return_value=alerts.Delivery(configured=True, delivered=True)
    )
    with sender as send:
        health.deliver_pending_alerts(now + timedelta(seconds=60))
    assert send.call_args.args[0] == "camera_degraded"
    incident.refresh_from_db()
    assert incident.degraded_alert_sent_at is not None


def test_degraded_incident_recovers_after_two_healthy_checks(monkeypatch):
    monkeypatch.setattr(health, "DEGRADED_THRESHOLD", 1)
    monkeypatch.setattr(health, "RECOVERY_THRESHOLD", 2)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.DEGRADED, 9), now)
    state, _ = health.record_observation(
        observation(CameraHealthState.HEALTHY, 10), now + timedelta(seconds=30)
    )
    assert state.status == CameraHealthState.DEGRADED
    state, incident_id = health.record_observation(
        observation(CameraHealthState.HEALTHY, 10), now + timedelta(seconds=60)
    )
    assert state.status == CameraHealthState.HEALTHY
    incident = CameraIncident.objects.get(pk=incident_id)
    assert incident.resolved_at == now + timedelta(seconds=60)


def test_alert_is_sent_once_per_transition(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.OUTAGE, 0), now)
    sender = patch.object(
        alerts, "send", return_value=alerts.Delivery(configured=True, delivered=True)
    )
    with sender as send:
        health.deliver_pending_alerts(now)
        health.deliver_pending_alerts(now + timedelta(seconds=health.ALERT_RETRY_SECONDS + 1))
    assert send.call_count == 1
    incident = CameraIncident.objects.get()
    assert incident.outage_alert_sent_at == now


def test_unconfigured_alert_is_throttled_and_kept_pending(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(health, "ALERT_RETRY_SECONDS", 900)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.OUTAGE, 0), now)
    sender = patch.object(
        alerts,
        "send",
        return_value=alerts.Delivery(
            configured=False, delivered=False, errors=("no alert destination configured",)
        ),
    )
    with sender as send:
        health.deliver_pending_alerts(now)
        health.deliver_pending_alerts(now + timedelta(seconds=100))
    assert send.call_count == 1
    incident = CameraIncident.objects.get()
    assert incident.outage_alert_sent_at is None
    assert "no alert" in incident.alert_error


def test_failed_outage_alert_survives_recovery_and_is_retried_in_order(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(health, "RECOVERY_THRESHOLD", 1)
    monkeypatch.setattr(health, "ALERT_RETRY_SECONDS", 900)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.OUTAGE, 0), now)

    sender = patch.object(
        alerts,
        "send",
        side_effect=[
            alerts.Delivery(configured=True, delivered=False, errors=("timeout",)),
            alerts.Delivery(configured=True, delivered=True),
            alerts.Delivery(configured=True, delivered=True),
        ],
    )
    with sender as send:
        health.deliver_pending_alerts(now)
        health.record_observation(
            observation(CameraHealthState.HEALTHY, 10), now + timedelta(seconds=30)
        )
        health.deliver_pending_alerts(
            now + timedelta(seconds=health.ALERT_RETRY_SECONDS + 1)
        )

    assert [call.args[0] for call in send.call_args_list] == [
        "camera_outage",
        "camera_outage",
        "camera_recovery",
    ]
    incident = CameraIncident.objects.get()
    assert incident.resolved_at is not None
    assert incident.outage_alert_sent_at is not None
    assert incident.recovery_alert_sent_at is not None


def test_partial_recovery_does_not_jump_a_failed_outage_alert(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(health, "RECOVERY_THRESHOLD", 1)
    monkeypatch.setattr(health, "ALERT_RETRY_SECONDS", 900)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.OUTAGE, 0), now)

    sender = patch.object(
        alerts,
        "send",
        return_value=alerts.Delivery(
            configured=True, delivered=False, errors=("timeout",)
        ),
    )
    with sender as send:
        health.deliver_pending_alerts(now)
        state, _ = health.record_observation(
            observation(CameraHealthState.DEGRADED, 9), now + timedelta(seconds=30)
        )
        health.deliver_pending_alerts(now + timedelta(seconds=30))

    assert state.status == CameraHealthState.DEGRADED
    assert [call.args[0] for call in send.call_args_list] == ["camera_outage"]
    incident = CameraIncident.objects.get()
    assert incident.severity == CameraIncident.OUTAGE
    assert incident.degraded_details == {}


def test_outage_supersedes_failed_degraded_alert_without_delaying_critical(monkeypatch):
    monkeypatch.setattr(health, "DEGRADED_THRESHOLD", 1)
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 1)
    now = timezone.now()
    health.record_observation(observation(CameraHealthState.DEGRADED, 9), now)

    sender = patch.object(
        alerts,
        "send",
        side_effect=[
            alerts.Delivery(configured=True, delivered=False, errors=("timeout",)),
            alerts.Delivery(configured=True, delivered=True),
        ],
    )
    with sender as send:
        health.deliver_pending_alerts(now)
        health.record_observation(
            observation(CameraHealthState.OUTAGE, 0), now + timedelta(seconds=30)
        )
        health.deliver_pending_alerts(now + timedelta(seconds=30))

    assert [call.args[0] for call in send.call_args_list] == [
        "camera_degraded",
        "camera_outage",
    ]
    incident = CameraIncident.objects.get()
    assert incident.severity == CameraIncident.OUTAGE
    assert incident.degraded_alert_sent_at is None
    assert incident.degraded_alert_superseded_at == now + timedelta(seconds=30)
    assert incident.outage_alert_sent_at == now + timedelta(seconds=30)


def test_stale_monitor_heartbeat_is_unavailable():
    now = timezone.now()
    state = CameraHealthState.objects.create(
        status=CameraHealthState.HEALTHY,
        observed_status=CameraHealthState.HEALTHY,
        expected_count=10,
        online_count=10,
        last_checked_at=now - timedelta(seconds=181),
    )
    payload = health.state_payload(state, now=now, max_age=180)
    assert payload["status"] == "unavailable"
    assert payload["recorded_status"] == CameraHealthState.HEALTHY
    assert payload["stale"] is True
    assert health.exit_code(payload) == 2


def test_fresh_zero_camera_probe_stays_pending_until_outage_confirmed(monkeypatch):
    monkeypatch.setattr(health, "FAILURE_THRESHOLD", 3)
    now = timezone.now()
    state, _ = health.record_observation(
        observation(CameraHealthState.HEALTHY, 10), now - timedelta(seconds=30)
    )
    state, _ = health.record_observation(
        observation(CameraHealthState.OUTAGE, 0), now
    )
    payload = health.state_payload(state, now=now, max_age=180)
    assert payload["recorded_status"] == CameraHealthState.HEALTHY
    assert payload["observed_status"] == CameraHealthState.OUTAGE
    assert payload["confirming_outage"] is True
    assert health.exit_code(payload) == 2


def test_deploy_gate_rejects_heartbeat_from_before_required_start():
    now = timezone.now()
    state = CameraHealthState.objects.create(
        status=CameraHealthState.HEALTHY,
        observed_status=CameraHealthState.HEALTHY,
        expected_count=10,
        online_count=10,
        last_checked_at=now - timedelta(seconds=5),
    )
    payload = health.state_payload(
        state,
        now=now,
        max_age=180,
        required_since=now - timedelta(seconds=1),
    )
    assert payload["fresh_since_required_start"] is False
    assert health.exit_code(payload) == 2


def test_scheduled_monitor_can_fail_on_degraded_without_blocking_deploy_gate():
    now = timezone.now()
    state = CameraHealthState.objects.create(
        status=CameraHealthState.DEGRADED,
        observed_status=CameraHealthState.DEGRADED,
        expected_count=10,
        online_count=9,
        last_checked_at=now,
    )
    payload = health.state_payload(state, now=now, max_age=180)
    assert health.exit_code(payload) == 0
    assert health.exit_code(payload, fail_on_degraded=True) == 4


def test_health_endpoint_reports_staff_state(auth_client, operator):
    CameraHealthState.objects.create(
        status=CameraHealthState.DEGRADED,
        observed_status=CameraHealthState.DEGRADED,
        expected_count=10,
        online_count=9,
        last_checked_at=timezone.now(),
    )
    response = auth_client(operator).get("/api/cameras/health/")
    assert response.status_code == 200
    assert response.data["status"] == CameraHealthState.DEGRADED
    assert response.data["online_count"] == 9


def test_health_endpoint_is_503_for_confirmed_outage(auth_client, operator):
    CameraHealthState.objects.create(
        status=CameraHealthState.OUTAGE,
        observed_status=CameraHealthState.OUTAGE,
        expected_count=10,
        online_count=0,
        last_checked_at=timezone.now(),
    )
    response = auth_client(operator).get("/api/cameras/health/")
    assert response.status_code == 503
    assert response.data["status"] == CameraHealthState.OUTAGE


def test_health_endpoint_denies_portal_client(auth_client, client_user):
    assert auth_client(client_user).get("/api/cameras/health/").status_code == 403


def test_check_command_uses_contract_exit_code_for_missing_heartbeat():
    with pytest.raises(SystemExit) as exc:
        call_command("check_camera_health", max_age=180)
    assert exc.value.code == 2
