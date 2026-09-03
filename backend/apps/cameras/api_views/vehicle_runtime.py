"""Safe projection and superuser ROI updates for vehicle recognition."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import ClassVar

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser, PermAPIViewMixin
from apps.grain.passage_scale_automation import scale_automation_runtime

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
    points = _project_points(
        roi.get("points"),
        "roi.points",
        configured=configured,
    )
    return {
        "cam": camera,
        "configured": configured,
        "enabled": enabled,
        "source": source,
        "coordinate_space": "normalized",
        "points": points,
        "updated_at": _text_or_none(roi.get("updated_at"), "roi.updated_at"),
    }


def _project_points(value, field: str, *, configured: bool = True) -> list[dict]:
    raw_points = value
    if not isinstance(raw_points, Sequence) or isinstance(
        raw_points, (str, bytes, bytearray)
    ):
        raise VehicleRuntimeContractError(f"{field} must be an array")
    if (configured and not 3 <= len(raw_points) <= 12) or (
        not configured and len(raw_points) != 0
    ):
        raise VehicleRuntimeContractError(f"{field} has an invalid shape")
    points: list[dict[str, float]] = []
    for index, raw_point in enumerate(raw_points):
        if isinstance(raw_point, Mapping):
            if set(raw_point) != {"x", "y"}:
                raise VehicleRuntimeContractError(
                    f"{field}[{index}] must contain only x and y"
                )
            raw_coordinates = (raw_point.get("x"), raw_point.get("y"))
        elif (
            isinstance(raw_point, Sequence)
            and not isinstance(raw_point, (str, bytes, bytearray))
            and len(raw_point) == 2
        ):
            raw_coordinates = (raw_point[0], raw_point[1])
        else:
            raise VehicleRuntimeContractError(
                f"{field}[{index}] must be [x, y] or an x/y object"
            )
        coordinates: dict[str, float] = {}
        for axis, raw_coordinate in zip(("x", "y"), raw_coordinates, strict=True):
            if isinstance(raw_coordinate, bool) or not isinstance(raw_coordinate, Real):
                raise VehicleRuntimeContractError("ROI coordinates must be numbers")
            coordinate = float(raw_coordinate)
            if not math.isfinite(coordinate) or not 0 <= coordinate <= 1:
                raise VehicleRuntimeContractError("ROI coordinates must be normalized")
            coordinates[axis] = coordinate
        points.append(coordinates)
    if configured:
        area = (
            sum(
                (points[index]["x"] * points[(index + 1) % len(points)]["y"])
                - (points[(index + 1) % len(points)]["x"] * points[index]["y"])
                for index in range(len(points))
            )
            / 2.0
        )
        if abs(area) < 0.0001:
            raise VehicleRuntimeContractError("vehicle ROI polygon has no area")
    return points


def project_vehicle_roi_update(value, *, expected_source: str = "main") -> dict:
    """Validate and canonicalize the browser body accepted by camera-PC."""
    expected_source = _source(expected_source, "expected_source")
    body = _mapping(value, "body")
    allowed_fields = {"points", "enabled", "source"}
    if set(body) - allowed_fields:
        raise VehicleRuntimeContractError("body contains unsupported fields")
    if "points" not in body:
        raise VehicleRuntimeContractError("points are required")
    result = {
        "points": _project_points(body.get("points"), "points"),
        "enabled": _boolean(body.get("enabled", True), "enabled"),
        "source": expected_source,
    }
    if "source" in body:
        source = _source(body.get("source"), "source")
        if source != expected_source:
            raise VehicleRuntimeContractError(
                "vehicle ROI source does not match the configured stream"
            )
    return result


def project_vehicle_roi_save_response(
    camera: str,
    value,
    *,
    expected_source: str = "main",
) -> dict:
    """Return only the persisted ROI state and safe apply flags."""
    expected_source = _source(expected_source, "expected_source")
    payload = _mapping(value, "saved ROI")
    saved = _boolean(payload.get("saved"), "saved")
    applied = _boolean(payload.get("applied_to_monitor"), "applied_to_monitor")
    if not saved:
        raise VehicleRuntimeContractError("saved must be true")
    roi = _project_roi(payload, camera)
    if not roi["configured"]:
        raise VehicleRuntimeContractError("saved ROI must be configured")
    if roi["source"] != expected_source:
        raise VehicleRuntimeContractError(
            "saved vehicle ROI source does not match the configured stream"
        )
    return {
        "saved": True,
        "applied_to_monitor": applied,
        "roi": roi,
    }


def _project_on_demand(value) -> dict:
    """Project the weight-triggered capability added by newer camera PCs.

    Missing data is treated as disabled so a rolling deployment can show a
    useful diagnostic while the camera PC is still on the previous release.
    """

    if value is None:
        return {"enabled": False, "cameras": []}
    on_demand = _mapping(value, "on_demand")
    enabled = _boolean(on_demand.get("enabled"), "on_demand.enabled")
    cameras = on_demand.get("cameras")
    if not isinstance(cameras, list) or not all(
        isinstance(item, str) and ai.CAM_RE.fullmatch(item) for item in cameras
    ):
        raise VehicleRuntimeContractError("on_demand.cameras is invalid")
    return {
        "enabled": enabled,
        "cameras": list(cameras),
    }


def _browser_stream(camera: str, source: str) -> str:
    """Map MediaMTX source names to provisioned go2rtc browser aliases."""
    return camera if source == "sub" else f"{camera}main"


def project_vehicle_runtime(camera: str, info, roi) -> dict:
    runtime = _mapping(info, "runtime")
    automation = _mapping(runtime.get("automation"), "automation")
    enabled = _boolean(runtime.get("enabled"), "enabled")
    ready = _boolean(runtime.get("ready"), "ready")
    automation_enabled = _boolean(automation.get("enabled"), "automation.enabled")
    server_push_configured = _boolean(
        automation.get("server_push_configured"), "automation.server_push_configured"
    )
    automation_source = _source(automation.get("source"), "automation.source")
    configured_cameras = automation.get("configured_cameras")
    if not isinstance(configured_cameras, list) or not all(
        isinstance(item, str) and ai.CAM_RE.fullmatch(item)
        for item in configured_cameras
    ):
        raise VehicleRuntimeContractError("automation.configured_cameras is invalid")
    monitors = _mapping(automation.get("monitors"), "automation.monitors")
    raw_monitor = monitors.get(camera)
    monitor = _project_monitor(raw_monitor, camera) if raw_monitor is not None else None
    if monitor is not None and monitor["source"] != automation_source:
        raise VehicleRuntimeContractError(
            "monitor.source does not match automation.source"
        )
    camera_configured = camera in configured_cameras
    on_demand = _project_on_demand(runtime.get("on_demand"))
    on_demand_camera_configured = camera in on_demand["cameras"]
    weight_first_enabled = bool(settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED)
    scale_automation = scale_automation_runtime()
    on_demand_required = weight_first_enabled or scale_automation["enabled"]
    projected_roi = _project_roi(roi, camera)
    source = (
        _source(
            settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE,
            "VEHICLE_PLATE_WEIGHT_FIRST_SOURCE",
        )
        if on_demand_required
        else automation_source
    )

    if not enabled:
        diagnostic = "model_disabled"
    elif not ready:
        diagnostic = "model_not_ready"
    elif on_demand_required and not on_demand["enabled"]:
        diagnostic = "on_demand_disabled"
    elif on_demand_required and not on_demand_camera_configured:
        diagnostic = "on_demand_camera_not_configured"
    elif on_demand_required and (
        not projected_roi["configured"] or not projected_roi["enabled"]
    ):
        diagnostic = "on_demand_roi_not_ready"
    elif on_demand_required and projected_roi["source"] != source:
        diagnostic = "on_demand_roi_source_mismatch"
    elif on_demand_required:
        diagnostic = "on_demand_ready"
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
        "weight_first_enabled": weight_first_enabled,
        "scale_automation": scale_automation,
        "on_demand_enabled": on_demand["enabled"],
        "on_demand_camera_configured": on_demand_camera_configured,
        "source": source,
        "stream": _browser_stream(camera, source),
        "server_push_configured": server_push_configured,
        "diagnostic": diagnostic,
        "monitor": monitor,
        "roi": projected_roi,
    }


def _error_response(detail: str, code: str, response_status: int) -> Response:
    response = Response({"detail": detail, "code": code}, status=response_status)
    response["Cache-Control"] = "no-store"
    return response


class VehiclePlateRuntimeView(PermAPIViewMixin, APIView):
    """Live diagnostics plus a superuser-only canonical ROI update."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get_permissions(self):
        if self.request.method.lower() == "put":
            return [IsSuperUser()]
        return super().get_permissions()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request, cam: str | None = None):
        if not ai.enabled():
            return _error_response(
                "AI-сервис камер не настроен",
                "ai_disabled",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            camera = ai.camera_id(cam or settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA)
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

    def put(self, request, cam: str):
        if not ai.enabled():
            return _error_response(
                "AI-сервис камер не настроен",
                "ai_disabled",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            camera = ai.camera_id(cam)
            expected_source = (
                settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE
                if (
                    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED
                    or settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED
                )
                and camera == settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA
                else "main"
            )
            update = project_vehicle_roi_update(
                request.data,
                expected_source=expected_source,
            )
        except VehicleRuntimeContractError:
            return _error_response(
                "Некорректная область распознавания",
                "invalid_vehicle_roi",
                status.HTTP_400_BAD_REQUEST,
            )
        except ai.AiError:
            return _error_response(
                "Неизвестная камера",
                "ai_error",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            upstream_status, upstream_payload = ai.save_vehicle_roi(camera, update)
            if upstream_status in (
                status.HTTP_200_OK,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ):
                try:
                    payload = project_vehicle_roi_save_response(
                        camera,
                        upstream_payload,
                        expected_source=expected_source,
                    )
                except VehicleRuntimeContractError:
                    return _error_response(
                        "AI-сервис вернул некорректный результат сохранения ROI",
                        "ai_invalid_response",
                        status.HTTP_502_BAD_GATEWAY,
                    )
                if upstream_status == status.HTTP_503_SERVICE_UNAVAILABLE:
                    payload.update(
                        {
                            "detail": "ROI сохранён, но монитор пока не применил обновление",
                            "code": "roi_saved_refresh_pending",
                        }
                    )
                return Response(payload, status=upstream_status)
            response_status = (
                upstream_status
                if upstream_status in (400, 404)
                else status.HTTP_502_BAD_GATEWAY
            )
            detail = {
                400: "Некорректная область распознавания",
                404: "Камера не найдена в AI-сервисе",
            }.get(response_status, "AI-сервис не сохранил область распознавания")
            return _error_response(detail, "ai_error", response_status)
        except ai.AiUnavailable:
            return _error_response(
                "AI-сервис камер недоступен",
                "ai_unavailable",
                status.HTTP_502_BAD_GATEWAY,
            )
        except ai.AiError as exc:
            response_status = (
                exc.status if exc.status in (400, 404) else status.HTTP_502_BAD_GATEWAY
            )
            detail = {
                400: "Неизвестная камера",
                404: "Камера не найдена в AI-сервисе",
            }.get(response_status, "AI-сервис не сохранил область распознавания")
            return _error_response(detail, "ai_error", response_status)
