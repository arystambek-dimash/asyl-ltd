"""Зона видимости камеры: маска, вне которой кадр не видят ни AI, ни экраны CRM."""

from typing import ClassVar

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser
from apps.common.viewsets import NoStoreMixin

from .. import ai, services
from .responses import error_response
from .vehicle_runtime import (
    PolygonEditor,
    VehicleRuntimeContractError,
    project_roi,
    save_polygon,
)

VISIBILITY_ZONE_EDITOR = PolygonEditor(
    key="zone",
    invalid="Некорректная зона видимости",
    invalid_code="invalid_visibility_zone",
    rejected="ПК камер не сохранил зону видимости",
    malformed="AI-сервис вернул некорректный результат сохранения зоны",
    pending="Зона сохранена, но камера пока не применила обновление",
    pending_code="zone_saved_refresh_pending",
)


class CameraVisibilityZoneView(NoStoreMixin, APIView):
    """Суперпользователь читает и меняет маску одной камеры на ПК камер."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request, cam: str):
        if not ai.enabled():
            return error_response(
                "AI-сервис камер не настроен",
                "ai_disabled",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            camera = ai.camera_id(cam)
            zone = project_roi(ai.visibility_zone(camera), camera)
        except ai.AiUnavailable:
            return error_response(
                "AI-сервис камер недоступен",
                "ai_unavailable",
                status.HTTP_502_BAD_GATEWAY,
            )
        except ai.AiError as exc:
            if exc.status == 400:
                return error_response(
                    "Неизвестная камера", "ai_error", status.HTTP_400_BAD_REQUEST
                )
            if exc.status == 404:
                # Старый AI-сервис не знает маршрута — 404 и на известную камеру.
                return error_response(
                    "ПК камер не поддерживает зону видимости для этой камеры — обновите AI-сервис",
                    "visibility_zone_unsupported",
                    status.HTTP_404_NOT_FOUND,
                )
            return error_response(
                "AI-сервис вернул ошибку", "ai_error", status.HTTP_502_BAD_GATEWAY
            )
        except VehicleRuntimeContractError:
            return error_response(
                "AI-сервис вернул некорректную зону видимости",
                "ai_invalid_response",
                status.HTTP_502_BAD_GATEWAY,
            )
        return Response(zone)

    def put(self, request, cam: str):
        response = save_polygon(
            cam,
            request.data,
            editor=VISIBILITY_ZONE_EDITOR,
            save=ai.save_visibility_zone,
            expected_source=lambda camera: "main",
        )
        if response.data.get("saved") is True:
            services.update_cached_visibility_zone(cam, response.data["zone"])
        return response
