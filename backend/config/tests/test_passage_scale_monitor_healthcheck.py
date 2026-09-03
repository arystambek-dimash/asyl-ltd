import json
from pathlib import Path

import passage_scale_monitor_healthcheck as healthcheck


def _write(path: Path, *, status: str, updated_at: object) -> None:
    path.write_text(
        json.dumps({"status": status, "updated_at": updated_at}),
        encoding="utf-8",
    )


def test_healthcheck_accepts_recent_live_states(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.json"

    for status in ("disabled", "running", "degraded"):
        _write(heartbeat, status=status, updated_at=1000.5)

        healthy, message = healthcheck.check_heartbeat(
            heartbeat,
            max_age_seconds=60,
            now=1001,
        )

        assert healthy
        assert f"status={status}" in message


def test_healthcheck_rejects_missing_malformed_and_oversized_files(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    assert not healthcheck.check_heartbeat(
        heartbeat,
        max_age_seconds=60,
        now=1000,
    )[0]

    heartbeat.write_text("not-json", encoding="utf-8")
    assert not healthcheck.check_heartbeat(
        heartbeat,
        max_age_seconds=60,
        now=1000,
    )[0]

    heartbeat.write_bytes(b" " * (healthcheck.MAX_HEARTBEAT_BYTES + 1))
    assert not healthcheck.check_heartbeat(
        heartbeat,
        max_age_seconds=60,
        now=1000,
    )[0]


def test_healthcheck_rejects_stale_future_and_invalid_payloads(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"

    _write(heartbeat, status="running", updated_at=1000)
    healthy, message = healthcheck.check_heartbeat(
        heartbeat,
        max_age_seconds=60,
        now=1061,
    )
    assert not healthy
    assert "stale" in message

    _write(heartbeat, status="running", updated_at=1061)
    healthy, message = healthcheck.check_heartbeat(
        heartbeat,
        max_age_seconds=60,
        now=1000,
    )
    assert not healthy
    assert "future" in message

    for payload in (
        [],
        {"status": "failed", "updated_at": 1000},
        {"status": "running", "updated_at": True},
        {"status": "running", "updated_at": "1000"},
    ):
        heartbeat.write_text(json.dumps(payload), encoding="utf-8")
        assert not healthcheck.check_heartbeat(
            heartbeat,
            max_age_seconds=60,
            now=1000,
        )[0]


def test_main_reads_configured_path_and_max_age(
    tmp_path: Path,
    monkeypatch,
) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    _write(heartbeat, status="running", updated_at=1000)
    monkeypatch.setenv(
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE",
        str(heartbeat),
    )
    monkeypatch.setenv(
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS",
        "10",
    )
    monkeypatch.setattr(healthcheck.time, "time", lambda: 1005)

    assert healthcheck.main() == 0
