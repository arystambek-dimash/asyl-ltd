"""Browser-facing view of the wagon arch: zone polygon, motion state, importer runtime."""
import logging
from typing import ClassVar

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import SUPERUSER_ONLY, PermAPIViewMixin
from apps.common.viewsets import NoStoreMixin
from apps.grain import wagon_arch

from .. import ai
from .vehicle_runtime import (
    PolygonEditor,
    VehicleRuntimeContractError,
    project_roi,
    save_polygon,
)

logger = logging.getLogger(__name__)

MOTION_STATES = {"moving", "still", "unknown"}
ARCH_ZONE_EDITOR = PolygonEditor(
    key="zone",
    invalid="Некорректная зона арки",
    invalid_code="invalid_arch_zone",
    rejected="ПК камер не сохранил зону арки",
    malformed="AI-сервис вернул некорректный результат сохранения зоны",
    pending="Зона сохранена, но монитор пока не применил обновление",
    pending_code="zone_saved_refresh_pending",
)


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
    return project_roi({"cam": camera, "configured": False, "enabled": False, "source": "main",
                        "coordinate_space": "normalized", "points": [], "updated_at": None}, camera)


def _diagnostic_detail(exc: Exception) -> str:
    """Safe, fixed Russian text for a browser-facing diagnostic — never raw internal text.

    ``ai.AiUnavailable``'s ``str()`` may carry the camera-PC host or a raw connection
    error; ``ai.AiError.detail`` and ``VehicleRuntimeContractError``'s message are raw
    English camera-PC/contract text. None is fit for a ``role="alert"`` shown to every
    ``grain.view`` user every 5 seconds. The raw exception is logged server-side.
    """
    logger.warning("Диагностика арки вагонных весов: %r", exc)
    if isinstance(exc, ai.AiError):
        if exc.status == 404:
            # Камера неизвестна ПК камер или у неё нет монитора арки.
            return "арка не настроена для этой камеры на ПК камер"
        if exc.status == 503:
            return "зоны арки не включены на ПК камер"
        return f"ПК камер ответил ошибкой {exc.status}"
    if isinstance(exc, VehicleRuntimeContractError):
        return "ПК камер вернул некорректный ответ"
    return "ПК камер недоступен"


class WagonArchCameraRuntimeView(NoStoreMixin, PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict[str, str]] = {
        "get": "grain.view",
        "put": SUPERUSER_ONLY,
    }

    def get(self, request):
        camera = settings.WAGON_ARCH_CAMERA
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
            payload["zone"] = project_roi(ai.arch_zone(camera), camera)
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Зона арки недоступна: {_diagnostic_detail(exc)}")
        try:
            payload["motion"] = _project_motion(ai.arch_motion(camera))
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Движение недоступно: {_diagnostic_detail(exc)}")
        payload["diagnostic"] = "; ".join(problems)
        return Response(payload)

    def put(self, request, cam: str):
        return save_polygon(
            cam,
            request.data,
            editor=ARCH_ZONE_EDITOR,
            save=ai.save_arch_zone,
            expected_source=lambda camera: "main",
        )
