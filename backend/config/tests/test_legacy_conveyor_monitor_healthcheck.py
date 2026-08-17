import json
import os
import time
import uuid

import legacy_conveyor_monitor_healthcheck as healthcheck


def test_legacy_monitor_healthcheck_main_accepts_fresh_live_heartbeat(
    tmp_path,
    monkeypatch,
    capsys,
):
    heartbeat = tmp_path / "legacy-heartbeat"
    heartbeat.write_text(
        json.dumps(
            {
                "version": healthcheck.HEARTBEAT_VERSION,
                "state": "ok",
                "timestamp": time.time(),
                "pid": os.getpid(),
                "boot_id": str(uuid.uuid4()),
                "db_backend_pid": 123,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_FILE",
        str(heartbeat),
    )

    assert healthcheck.main() == 0
    assert "healthy" in capsys.readouterr().out


def test_legacy_monitor_healthcheck_main_rejects_missing_heartbeat(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv(
        "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_FILE",
        str(tmp_path / "missing"),
    )

    assert healthcheck.main() == 1
    assert "unavailable" in capsys.readouterr().err
