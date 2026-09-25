"""Camera-PC plate answers shared by the manual and automatic weight captures.

Both :class:`PassageWeightCapture` (operator-triggered) and
:class:`AutomaticPassageCapture` (scale edge) send one stable-weight trigger to
Camera-PC and store the confirmed plate the same way. The collector importer
keeps its refusals through the same bounded diagnostics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from rest_framework.exceptions import APIException

from apps.cameras import ai as camera_ai
from apps.common.datetimes import parse_aware_datetime

from .models import RecognitionCapture

# ``no_match`` answers carry the last OCR reads and the vote tally so an
# operator can tell a detector miss from a misread. They are copied within
# the same bounds the Camera-PC promises, never trusted for size.
MAX_NO_MATCH_VOTES = 8
MAX_NO_MATCH_READS = 8
MAX_NO_MATCH_TEXT = 32

# Fields :func:`apply_recognition` writes; callers add their own extras.
RECOGNITION_FIELDS = (
    "vehicle_number",
    "camera_source",
    "recognized_at",
    "confirmation_votes",
    "detector_confidence",
    "ocr_confidence",
    "ai_payload_json",
    "response_status",
    "retryable",
    "error_code",
    "error_detail",
    "stage",
)


def canonical_timestamp(value: datetime) -> str:
    """The stable-weight trigger exactly as Camera-PC echoes it back."""

    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def api_exception_parts(error: APIException) -> tuple[str, str]:
    """``(detail, code)`` of a DRF error, bounded to the capture's columns."""

    details = error.detail
    codes = error.get_codes()
    if isinstance(details, dict):
        raw_detail = details.get("detail")
        detail = str(raw_detail) if raw_detail else str(error)
    else:
        detail = str(details)
    if isinstance(details, dict) and details.get("code"):
        code = str(details["code"])
    elif isinstance(codes, dict):
        raw_code = codes.get("code") or codes.get("detail")
        code = str(raw_code) if raw_code else "passage_capture_rejected"
    else:
        code = str(codes or "passage_capture_rejected")
    return detail[:300], code[:64]


def terminal_ai_error(error: camera_ai.AiError) -> tuple[int, str, str, bool]:
    """``(status_code, code, detail, retryable)`` for a Camera-PC refusal."""

    payload = error.payload
    remote_status = str(payload.get("status") or "")
    if error.status in {401, 403}:
        return (
            502,
            "vehicle_recognition_auth_failed",
            "Camera-PC отклонил служебный ключ. Обратитесь к администратору.",
            False,
        )
    retryable_hint = payload.get("retryable")
    # A reverse proxy may return an HTML/empty 5xx response before the CV
    # service can attach its retryability contract. Preserve the stored scale
    # sample in that case: only an explicit ``retryable: false`` may make a
    # remote 5xx terminal.
    retryable = (
        retryable_hint is True
        or error.status == 202
        or (error.status >= 500 and retryable_hint is not False)
    )
    codes = {
        "processing": "vehicle_recognition_pending",
        "model_unavailable": "vehicle_model_unavailable",
        "on_demand_unavailable": "vehicle_recognition_not_configured",
        "no_match": "vehicle_plate_not_confirmed",
        "camera_unavailable": "vehicle_camera_unavailable",
        "roi_unavailable": "vehicle_roi_unavailable",
        "stale_weight_trigger": "stale_weight_trigger",
        "camera_not_configured": "vehicle_camera_not_configured",
        "idempotency_conflict": "vehicle_recognition_idempotency_conflict",
        "lane_busy": "vehicle_recognition_lane_busy",
        "capture_window_missed": "vehicle_capture_window_missed",
        "failed": "vehicle_recognition_failed",
        "interrupted": "vehicle_recognition_interrupted",
    }
    code = codes.get(remote_status, "vehicle_recognition_failed")
    status_code = 409 if error.status == 202 else int(error.status)
    return status_code, code, error.detail, retryable


def safe_ai_payload(payload: Mapping | None) -> dict:
    """Keep bounded diagnostics, never an arbitrary upstream JSON document."""

    if not isinstance(payload, Mapping):
        return {}
    safe: dict[str, object] = {}
    string_limits = {
        "status": 64,
        "request_id": 36,
        "camera": 32,
        "source": 4,
        "stable_weight_at": 48,
        "recognized_at": 48,
        "vehicle_number": 30,
        "error": 300,
        "error_code": 64,
        "camera_status": 64,
        "active_input": 64,
        "roi_updated_at": 64,
    }
    for field, limit in string_limits.items():
        value = payload.get(field)
        if isinstance(value, str):
            safe[field] = value[:limit]
    for field in ("ok", "retryable", "votes_truncated"):
        value = payload.get(field)
        if isinstance(value, bool):
            safe[field] = value
    for field in (
        "fresh_frames_seen",
        "frames_scanned",
        "ambiguous_frames",
        "detected_frames",
        "ocr_candidates",
        "accepted_reads",
        "confirmation_votes",
        # The Camera-PC's second look at zoomed tiles of the frames that held
        # no plate, and how many of those looks found one.
        "zoom_frames",
        "zoom_detected_frames",
    ):
        count = _bounded_count(payload.get(field))
        if count is not None:
            safe[field] = count
    for field in ("best_detector_confidence", "confirmation_window_seconds"):
        number = _bounded_float(payload.get(field), upper=1e6 if field.endswith("seconds") else 1)
        if number is not None:
            safe[field] = number
    _copy_no_match_diagnostics(payload, safe)

    orientation = payload.get("orientation")
    if isinstance(orientation, Mapping):
        label, confidence = camera_ai.vehicle_orientation(payload)
        safe_orientation: dict[str, object] = {"label": label or None}
        if confidence is not None:
            safe_orientation["confidence"] = confidence
        raw_label = orientation.get("raw_label")
        if isinstance(raw_label, str):
            safe_orientation["raw_label"] = raw_label[:16]
        safe["orientation"] = safe_orientation

    confirmation = payload.get("confirmation")
    if isinstance(confirmation, Mapping):
        safe_confirmation: dict[str, int | float] = {}
        votes = confirmation.get("votes")
        if (
            isinstance(votes, int)
            and not isinstance(votes, bool)
            and 1 <= votes <= camera_ai.MAX_VEHICLE_CONFIRMATION_VOTES
        ):
            safe_confirmation["votes"] = votes
        for field in ("detector_confidence", "ocr_confidence"):
            number = _bounded_float(confirmation.get(field), upper=1)
            if number is not None:
                safe_confirmation[field] = number
        if safe_confirmation:
            safe["confirmation"] = safe_confirmation
    return safe


def _bounded_float(value: object, *, upper: float) -> float | None:
    """A finite float inside ``[0, upper]``, else ``None``."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= upper else None


def _bounded_count(value: object) -> int | None:
    """A non-negative integer capped at one million, else ``None``."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return min(value, 1_000_000)


def _copy_no_match_diagnostics(payload: Mapping, safe: dict) -> None:
    votes = payload.get("votes")
    if isinstance(votes, Mapping):
        safe_votes: dict[str, int] = {}
        for number, count in list(votes.items())[:MAX_NO_MATCH_VOTES]:
            bounded = _bounded_count(count)
            if (
                isinstance(number, str)
                and 1 <= len(number) <= MAX_NO_MATCH_TEXT
                and bounded is not None
            ):
                safe_votes[number] = bounded
        if safe_votes:
            safe["votes"] = safe_votes
    reads = payload.get("last_reads")
    if not isinstance(reads, list):
        return
    safe_reads: list[dict[str, object]] = []
    for read in reads[:MAX_NO_MATCH_READS]:
        if not isinstance(read, Mapping):
            continue
        item: dict[str, object] = {}
        frame = _bounded_count(read.get("frame"))
        if frame is not None:
            item["frame"] = frame
        for field in ("variant", "raw_text", "number"):
            value = read.get(field)
            if isinstance(value, str):
                item[field] = value[:MAX_NO_MATCH_TEXT]
        for field, upper in (
            ("confidence", 1),
            ("detector_confidence", 1),
            ("bbox_w", 100_000),
            ("bbox_h", 100_000),
        ):
            number = _bounded_float(read.get(field), upper=upper)
            if number is not None:
                item[field] = number
        if item:
            safe_reads.append(item)
    if safe_reads:
        safe["last_reads"] = safe_reads


def recognized_at_for(payload: Mapping, expected_trigger: datetime | None) -> datetime | None:
    """When the plate was read, if the answer belongs to ``expected_trigger``.

    ``None`` means malformed timestamps or an answer to another stable-weight
    trigger; the caller rejects it with its own error.
    """

    recognized_at = parse_aware_datetime(payload.get("recognized_at"))
    response_trigger = parse_aware_datetime(payload.get("stable_weight_at"))
    if (
        recognized_at is None
        or response_trigger is None
        or expected_trigger is None
        or response_trigger != expected_trigger
    ):
        return None
    return recognized_at


def apply_recognition(
    capture: RecognitionCapture,
    payload: Mapping,
    *,
    recognized_at: datetime,
    safe_payload: dict | None = None,
) -> None:
    """Copy a confirmed Camera-PC plate onto the capture; saving is the caller's.

    Clears the previous attempt's error and moves the capture to ``APPLYING``;
    every written column is listed in :data:`RECOGNITION_FIELDS`.
    """

    confirmation = payload["confirmation"]
    capture.vehicle_number = str(payload["vehicle_number"])
    capture.camera_source = str(payload["source"])
    capture.recognized_at = recognized_at
    capture.confirmation_votes = int(confirmation["votes"])
    capture.detector_confidence = Decimal(str(confirmation["detector_confidence"]))
    capture.ocr_confidence = Decimal(str(confirmation["ocr_confidence"]))
    capture.ai_payload_json = (
        safe_ai_payload(payload) if safe_payload is None else safe_payload
    )
    capture.response_status = 200
    capture.retryable = False
    capture.error_code = ""
    capture.error_detail = ""
    capture.stage = capture.APPLYING
