"""Bridge to recordings stored by MediaMTX on the camera PC.

No video bytes are persisted by Django. MediaMTX owns recording, retention and
playback; this module only lists a session's local segments and streams one to
an authenticated staff browser.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .services import CAMERA_HOST


PLAYBACK_URL = (
    os.environ.get("CAMERA_PLAYBACK_URL") or f"http://{CAMERA_HOST}:9996"
).rstrip("/")
TIMEOUT = 10
VIDEO_RETENTION_DAYS = 14


class RecordingUnavailable(Exception):
    pass


def _request(path: str):
    try:
        return urllib.request.urlopen(
            urllib.request.Request(f"{PLAYBACK_URL}{path}", method="GET"),
            timeout=TIMEOUT,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RecordingUnavailable(str(exc)) from exc


def list_segments(stream: str, start: datetime, end: datetime) -> list[dict]:
    query = urllib.parse.urlencode({
        "path": stream,
        "start": start.isoformat(),
        "end": end.isoformat(),
    })
    response = _request(f"/list?{query}")
    try:
        payload = json.loads(response.read() or b"[]")
    except (ValueError, TypeError) as exc:
        raise RecordingUnavailable("MediaMTX вернул некорректный архив") from exc
    finally:
        response.close()
    if not isinstance(payload, list):
        raise RecordingUnavailable("MediaMTX вернул некорректный архив")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        started = item.get("start")
        duration = item.get("duration")
        if not isinstance(started, str):
            continue
        try:
            duration = float(duration)
            datetime.fromisoformat(started.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if 0 < duration <= 24 * 3600:
            result.append({"start": started, "duration": duration})
    return result


def open_segment(stream: str, start: str, duration: float):
    query = urllib.parse.urlencode({
        "path": stream,
        "start": start,
        "duration": duration,
        # MediaMTX calls fragmented MP4 "fmp4"; the returned MIME remains
        # video/mp4 and is directly playable by the browser.
        "format": "fmp4",
    })
    return _request(f"/get?{query}")


def delete_session_segments(stream: str, start: datetime, end: datetime) -> int:
    """Delete all local MediaMTX files intersecting one counting session."""
    from . import ai

    segments = list_segments(stream, start, end)
    starts = [segment["start"] for segment in segments]
    if not starts:
        return 0
    try:
        payload = ai.delete_recordings(stream, starts)
    except (ai.AiUnavailable, ai.AiError) as exc:
        raise RecordingUnavailable(str(exc)) from exc
    return int(payload.get("deleted", 0))
