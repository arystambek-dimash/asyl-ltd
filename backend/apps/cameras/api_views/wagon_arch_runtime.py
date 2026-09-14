"""Browser-facing view of the wagon arch: zone polygon, motion state, importer runtime."""
import logging
from typing import ClassVar

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser, PermAPIViewMixin
from apps.grain import wagon_arch

from .. import ai
from .vehicle_runtime import (
    VehicleRuntimeContractError,
    _error_response,
    _project_roi,
    project_vehicle_roi_update,
)

logger = logging.getLogger(__name__)

MOTION_STATES = {"moving", "still", "unknown"}


def _project_motion(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    still = value.get("still_seconds")
    age = value.get("sample_age_seconds")
    if state not in MOTION_STATES or isinstance(still, bool) or not isinstance(still, (int, float)) or still < 0:
        raise VehicleRuntimeContractError("arch motion payload is malformed")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0):
        raise VehicleRuntimeContractError("arch motion age is malformed")
    return {
        "state": state,
        "still_seconds": float(still),
        "direction": str(value.get("direction") or ""),
        "status": str(value.get("status") or ""),
        "sample_age_seconds": float(age) if age is not None else None,
    }


def _empty_zone(camera: str) -> dict:
    return _project_roi({"cam": camera, "configured": False, "enabled": False, "source": "main",
                         "coordinate_space": "normalized", "points": [], "updated_at": None}, camera)


def _diagnostic_detail(exc: Exception) -> str:
    """Safe, fixed Russian text for a browser-facing diagnostic — never raw internal text.

    ``ai.AiUnavailable``'s ``str()`` may carry the camera-PC host or a raw connection
    error; ``VehicleRuntimeContractError``'s message is an internal English contract
    description. Neither is fit for a ``role="alert"`` shown to every ``grain.view``
    user every 5 seconds. ``ai.AiError.detail`` is already sanitised upstream in
    ``ai.py``, so it is safe to use as-is. The raw exception is logged server-side.
    """
    logger.warning("Диагностика арки вагонных весов: %r", exc)
    if isinstance(exc, ai.AiError):
        if exc.status == 404:
            # Старый сервис на ПК камер ещё не знает про зону арки.
            return "на ПК камер ещё нет функции зоны арки — обновите сервис"
        return exc.detail
    if isinstance(exc, VehicleRuntimeContractError):
        return "ПК камер вернул некорректный ответ"
    return "ПК камер недоступен"


class WagonArchCameraRuntimeView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get_permissions(self):
        if self.request.method.lower() == "put":
            return [IsSuperUser()]
        return super().get_permissions()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request, cam=None):
        try:
            camera = settings.WAGON_ARCH_CAMERA if cam is None else ai.camera_id(cam)
        except ai.AiError:
            return _error_response("Неизвестная камера", "ai_error", status.HTTP_400_BAD_REQUEST)
        payload = {
            "camera": camera, "source": "main", "stream": camera,
            "automation_enabled": bool(settings.WAGON_ARCH_AUTOMATION_ENABLED),
            "zone": _empty_zone(camera), "motion": None, "runtime": wagon_arch.runtime(), "diagnostic": "",
        }
        if not ai.enabled():
            payload["diagnostic"] = "AI-сервис камер не настроен"
            return Response(payload)
        problems = []
        try:
            payload["zone"] = _project_roi(ai.arch_zone(camera), camera)
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Зона арки недоступна: {_diagnostic_detail(exc)}")
        try:
            payload["motion"] = _project_motion(ai.arch_motion(camera))
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Движение недоступно: {_diagnostic_detail(exc)}")
        payload["diagnostic"] = "; ".join(problems)
        return Response(payload)

    def put(self, request, cam: str):
        if not ai.enabled():
            return _error_response("AI-сервис камер не настроен", "ai_disabled", status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            camera = ai.camera_id(cam)
            update = project_vehicle_roi_update(request.data, expected_source="main")
        except VehicleRuntimeContractError:
            return _error_response("Некорректная зона арки", "invalid_arch_zone", status.HTTP_400_BAD_REQUEST)
        except ai.AiError:
            return _error_response("Неизвестная камера", "ai_error", status.HTTP_400_BAD_REQUEST)
        try:
            upstream_status, upstream_payload = ai.save_arch_zone(camera, update)
        except ai.AiUnavailable:
            return _error_response("ПК камер недоступен", "ai_unavailable", status.HTTP_502_BAD_GATEWAY)
        if upstream_status in (status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE):
            try:
                zone = _project_roi(upstream_payload, camera)
            except VehicleRuntimeContractError:
                return _error_response("AI-сервис вернул некорректный результат сохранения зоны", "ai_invalid_response", status.HTTP_502_BAD_GATEWAY)
            payload = {"saved": bool(upstream_payload.get("saved", True)), "applied_to_monitor": bool(upstream_payload.get("applied_to_monitor", False)), "zone": zone}
            if upstream_status == status.HTTP_503_SERVICE_UNAVAILABLE:
                payload.update({"detail": "Зона сохранена, но монитор пока не применил обновление", "code": "zone_saved_refresh_pending"})
            return Response(payload, status=upstream_status)
        response_status = upstream_status if upstream_status in (400, 404) else status.HTTP_502_BAD_GATEWAY
        return _error_response(str(upstream_payload.get("error") or "ПК камер отклонил зону"), "ai_error", response_status)
