from pathlib import Path

from apipay_monitor_healthcheck import check_heartbeat


def test_apipay_monitor_healthcheck_accepts_recent_running_and_ok_states(
    tmp_path: Path,
):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("running 1000\n", encoding="utf-8")

    assert check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1001,
    )[0]

    heartbeat.write_text("ok 1002\n", encoding="utf-8")
    assert check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1003,
    )[0]


def test_apipay_monitor_healthcheck_rejects_error_and_stale_states(
    tmp_path: Path,
):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("error 1000\n", encoding="utf-8")
    healthy, message = check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1001,
    )
    assert not healthy
    assert "state=error" in message

    heartbeat.write_text("ok 1000\n", encoding="utf-8")
    healthy, message = check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1601,
    )
    assert not healthy
    assert "stale" in message


def test_apipay_monitor_healthcheck_rejects_missing_or_malformed_file(
    tmp_path: Path,
):
    heartbeat = tmp_path / "heartbeat"
    assert not check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1000,
    )[0]

    heartbeat.write_text("not-a-heartbeat\n", encoding="utf-8")
    assert not check_heartbeat(
        heartbeat,
        max_age_seconds=600,
        now=1000,
    )[0]
