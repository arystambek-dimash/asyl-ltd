from pathlib import Path
from unittest.mock import patch

from celery_beat_healthcheck import check_pidfile


def test_beat_healthcheck_accepts_a_live_pid(tmp_path: Path) -> None:
    pidfile = tmp_path / "celerybeat.pid"
    pidfile.write_text("42\n", encoding="utf-8")

    with patch("celery_beat_healthcheck.os.kill") as kill:
        healthy, message = check_pidfile(pidfile)

    assert healthy
    assert "42" in message
    kill.assert_called_once_with(42, 0)


def test_beat_healthcheck_rejects_missing_invalid_and_dead_pid(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "celerybeat.pid"
    assert not check_pidfile(pidfile)[0]

    pidfile.write_text("not-a-pid\n", encoding="utf-8")
    assert not check_pidfile(pidfile)[0]

    pidfile.write_text("43\n", encoding="utf-8")
    with patch(
        "celery_beat_healthcheck.os.kill",
        side_effect=ProcessLookupError,
    ):
        healthy, message = check_pidfile(pidfile)
    assert not healthy
    assert "not running" in message
