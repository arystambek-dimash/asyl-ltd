"""Safe read-only projection of the camera-PC vehicle recognition runtime."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import ClassVar

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import PermAPIViewMixin

from .. import ai

_SOURCES = frozenset({"main", "sub"})
_MONITOR_COUNTERS = (
    "scanned_frames",
    "plate_detections",
    "stationary_admissions",
    "ocr_attempts",
    "confirmed_events",
    "durable_duplicates",
    "consecutive_errors",
)
_MONITOR_TIMINGS = ("inference_avg_ms", "ocr_avg_ms")
_OPTIONAL_TEXT = (
    "started_at",
    "last_frame_at",
    "last_inference_at",
    "last_confirmed_at",
)


class VehicleRuntimeContractError(ValueError):
    """The trusted camera service returned a malformed runtime document."""


def _mapping(value, field: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise VehicleRuntimeContractError(f"{field} must be an object")
    return value


def _boolean(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise VehicleRuntimeContractError(f"{field} must be boolean")
    return value


def _source(value, field: str) -> str:
    if not isinstance(value, str) or value not in _SOURCES:
        raise VehicleRuntimeContractError(f"{field} must be main or sub")
    return value


def _text_or_none(value, field: str, *, limit: int = 128) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise VehicleRuntimeContractError(f"{field} must be short text or null")
    return value


def _non_negative_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VehicleRuntimeContractError(f"{field} must be a non-negative integer")
    return value


def _non_negative_number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise VehicleRuntimeContractError(f"{field} must be a non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise VehicleRuntimeContractError(
            f"{field} must be a finite non-negative number"
        )
    return number


def _project_stop_gate(value) -> dict:
    gate = _mapping(value, "monitor.stop_gate")
    return {
        "dwell_seconds": _non_negative_number(
            gate.get("dwell_seconds"), "stop_gate.dwell_seconds"
        ),
        "min_frames": _non_negative_int(gate.get("min_frames"), "stop_gate.min_frames"),
        "max_movement_ratio": _non_negative_number(
            gate.get("max_movement_ratio"), "stop_gate.max_movement_ratio"
        ),
        "exit_grace_seconds": _non_negative_number(
            gate.get("exit_grace_seconds"), "stop_gate.exit_grace_seconds"
        ),
    }


def _project_monitor(value, camera: str) -> dict:
    monitor = _mapping(value, "monitor")
    if monitor.get("cam") != camera:
        raise VehicleRuntimeContractError(
            "monitor.cam does not match the requested camera"
        )
    status_text = monitor.get("status")
    if not isinstance(status_text, str) or not status_text or len(status_text) > 64:
        raise VehicleRuntimeContractError("monitor.status must be short text")
    result = {
        "status": status_text,
        "source": _source(monitor.get("source"), "monitor.source"),
        "has_error": _non_negative_int(
            monitor.get("consecutive_errors"), "monitor.consecutive_errors"
        )
        > 0,
        "stop_gate": _project_stop_gate(monitor.get("stop_gate")),
    }
    for field in _OPTIONAL_TEXT:
        result[field] = _text_or_none(monitor.get(field), f"monitor.{field}")
    for field in _MONITOR_COUNTERS:
        result[field] = _non_negative_int(monitor.get(field), f"monitor.{field}")
    for field in _MONITOR_TIMINGS:
        result[field] = _non_negative_number(monitor.get(field), f"monitor.{field}")
    return result


def _project_roi(value, camera: str) -> dict:
    roi = _mapping(value, "roi")
    if roi.get("cam") != camera:
        raise VehicleRuntimeContractError("roi.cam does not match the requested camera")
    configured = _boolean(roi.get("configured"), "roi.configured")
    enabled = _boolean(roi.get("enabled"), "roi.enabled")
    source = _source(roi.get("source"), "roi.source")
    if roi.get("coordinate_space") != "normalized":
        raise VehicleRuntimeContractError("roi.coordinate_space must be normalized")
    raw_points = roi.get("points")
    if (
        not isinstance(raw_points, Sequence)
        or isinstance(raw_points, (str, bytes, bytearray))
        or len(raw_points) > 12
        or (configured and len(raw_points) < 3)
        or (not configured and len(raw_points) != 0)
    ):
        raise VehicleRuntimeContractError("roi.points has an invalid shape")
    points: list[dict[str, float]] = []
    for index, raw_point in enumerate(raw_points):
        point = _mapping(raw_point, f"roi.points[{index}]")
        coordinates: dict[str, float] = {}
        for axis in ("x", "y"):
            raw_coordinate = point.get(axis)
            if isinstance(raw_coordinate, bool) or not isinstance(raw_coordinate, Real):
                raise VehicleRuntimeContractError("ROI coordinates must be numbers")
            coordinate = float(raw_coordinate)
            if not math.isfinite(coordinate) or not 0 <= coordinate <= 1:
                raise VehicleRuntimeContractError("ROI coordinates must be normalized")
            coordinates[axis] = coordinate
        points.append(coordinates)
    return {
        "cam": camera,
        "configured": configured,
        "enabled": enabled,
        "source": source,
        "coordinate_space": "normalized",
        "points": points,
        "updated_at": _text_or_none(roi.get("updated_at"), "roi.updated_at"),
    }


def project_vehicle_runtime(camera: str, info, roi) -> dict:
    runtime = _mapping(info, "runtime")
    automation = _mapping(runtime.get("automation"), "automation")
    enabled = _boolean(runtime.get("enabled"), "enabled")
    ready = _boolean(runtime.get("ready"), "ready")
    automation_enabled = _boolean(automation.get("enabled"), "automation.enabled")
    server_push_configured = _boolean(
        automation.get("server_push_configured"), "automation.server_push_configured"
    )
    source = _source(automation.get("source"), "automation.source")
    configured_cameras = automation.get("configured_cameras")
    if not isinstance(configured_cameras, list) or not all(
        isinstance(item, str) and ai.CAM_RE.fullmatch(item)
        for item in configured_cameras
    ):
        raise VehicleRuntimeContractError("automation.configured_cameras is invalid")
    monitors = _mapping(automation.get("monitors"), "automation.monitors")
    raw_monitor = monitors.get(camera)
    monitor = _project_monitor(raw_monitor, camera) if raw_monitor is not None else None
    if monitor is not None and monitor["source"] != source:
        raise VehicleRuntimeContractError(
            "monitor.source does not match automation.source"
        )
    camera_configured = camera in configured_cameras

    if not enabled:
        diagnostic = "model_disabled"
    elif not ready:
        diagnostic = "model_not_ready"
    elif not automation_enabled:
        diagnostic = "automation_disabled"
    elif not camera_configured:
        diagnostic = "camera_not_configured"
    elif monitor is None:
        diagnostic = "monitor_missing"
    else:
        diagnostic = monitor["status"]

    return {
        "camera": camera,
        "enabled": enabled,
        "ready": ready,
        "automation_enabled": automation_enabled,
        "camera_configured": camera_configured,
        "source": source,
        "server_push_configured": server_push_configured,
        "diagnostic": diagnostic,
        "monitor": monitor,
        "roi": _project_roi(roi, camera),
    }


def _error_response(detail: str, code: str, response_status: int) -> Response:
    response = Response({"detail": detail, "code": code}, status=response_status)
    response["Cache-Control"] = "no-store"
    return response


class VehiclePlateRuntimeView(PermAPIViewMixin, APIView):
    """Live vehicle-model diagnostics and ROI without camera-PC secrets."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get(self, request, cam: str):
        if not ai.enabled():
            return _error_response(
                "AI-сервис камер не настроен",
                "ai_disabled",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            camera = ai.camera_id(cam)
            payload = project_vehicle_runtime(
                camera,
                ai.vehicle_number_info(),
                ai.vehicle_roi(camera),
            )
        except ai.AiUnavailable:
            return _error_response(
                "AI-сервис камер недоступен",
                "ai_unavailable",
                status.HTTP_502_BAD_GATEWAY,
            )
        except ai.AiError as exc:
            response_status = (
                exc.status
                if exc.status in (400, 404, 503)
                else status.HTTP_502_BAD_GATEWAY
            )
            detail = {
                400: "Неизвестная камера",
                404: "Камера не найдена в AI-сервисе",
                503: "Модель номеров временно недоступна",
            }.get(response_status, "AI-сервис вернул ошибку")
            return _error_response(detail, "ai_error", response_status)
        except VehicleRuntimeContractError:
            return _error_response(
                "AI-сервис вернул некорректный статус модели",
                "ai_invalid_response",
                status.HTTP_502_BAD_GATEWAY,
            )
        response = Response(payload)
        response["Cache-Control"] = "no-store"
        return response
