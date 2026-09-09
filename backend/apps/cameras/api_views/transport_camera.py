"""Superuser configuration and explicit OCR checks for shipping conveyors."""

from typing import ClassVar
from http.client import HTTPException
import math

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from PIL import Image

from apps.common.permissions import IsSuperUser
from apps.eventlog.services import log_event

from .. import ai, services, shipping_segment_identity as identity
from ..models import MonoblockCameraSettings, ShippingTransportCamera
from ..policies import assert_camera_has_no_active_work
from ..sessions import lock_camera_binding


class TransportCameraConflict(APIException):
    status_code = 409


class CameraInventoryUnavailable(APIException):
    status_code = 503


class TransportCameraSerializer(serializers.Serializer):
    number_camera = serializers.JSONField()
    recognition_model = serializers.ChoiceField(
        choices=ShippingTransportCamera.RECOGNITION_MODELS
    )
    loading_zone = serializers.JSONField(required=False, allow_null=True)

    def validate_loading_zone(self, value):
        if value is None:
            return None
        if (
            not isinstance(value, list)
            or len(value) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in value)
            or value[0] >= value[2]
            or value[1] >= value[3]
        ):
            raise ValidationError("Задайте прямоугольную зону внутри изображения")
        return value

    def validate_number_camera(self, value):
        if (
            not isinstance(value, str)
            or len(value) > 32
            or not ai.CAM_RE.fullmatch(value)
        ):
            raise ValidationError("Выберите камеру из списка")
        return value


def _conveyor(camera: str) -> str:
    if len(camera) > 32 or not ai.CAM_RE.fullmatch(camera):
        raise ValidationError({"detail": "Некорректная камера", "code": "bad_camera"})
    if camera not in MonoblockCameraSettings.shipping_sources():
        raise NotFound("Конвейер не включён в камеры отгрузки")
    return camera


def _payload(camera: str, binding: ShippingTransportCamera | None) -> dict:
    return {
        "conveyor_camera": camera,
        "number_camera": binding.number_camera if binding else None,
        "recognition_model": binding.recognition_model if binding else None,
        "loading_zone": binding.loading_zone if binding else None,
        "updated_at": binding.updated_at if binding else None,
    }


class ShippingTransportCameraView(APIView):
    permission_classes: ClassVar[list] = [IsSuperUser]

    def get(self, request, cam: str):
        camera = _conveyor(cam)
        binding = ShippingTransportCamera.objects.filter(conveyor_camera=camera).first()
        return Response(_payload(camera, binding))

    def put(self, request, cam: str):
        camera = _conveyor(cam)
        serializer = TransportCameraSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        number_camera = serializer.validated_data["number_camera"]
        recognition_model = serializer.validated_data["recognition_model"]
        if number_camera == camera:
            raise ValidationError(
                {
                    "detail": "Выберите отдельную камеру номера, которая видит транспорт",
                    "code": "same_camera",
                }
            )
        # Discovery may perform network I/O. Never keep the global session
        # reservation lock while waiting for the camera PC.
        known_cameras = {item.get("src") for item in services.discover_cameras()}
        with transaction.atomic():
            lock_camera_binding()
            _conveyor(camera)  # Recheck against removal while discovery ran.
            binding = ShippingTransportCamera.objects.filter(
                conveyor_camera=camera
            ).first()
            loading_zone = serializer.validated_data.get(
                "loading_zone",
                binding.loading_zone if binding and binding.number_camera == number_camera else None,
            )
            if binding and (binding.number_camera, binding.recognition_model, binding.loading_zone) == (
                number_camera,
                recognition_model,
                loading_zone,
            ):
                return Response(_payload(camera, binding))
            assert_camera_has_no_active_work(camera)
            if not known_cameras:
                raise CameraInventoryUnavailable(
                    {
                        "detail": "Список камер недоступен. Обновите его и повторите сохранение",
                        "code": "camera_inventory_unavailable",
                    }
                )
            if number_camera not in known_cameras:
                raise ValidationError(
                    {
                        "detail": "Выбранной камеры нет в списке доступных камер",
                        "code": "unknown_number_camera",
                    }
                )
            if (
                ShippingTransportCamera.objects.filter(number_camera=number_camera)
                .exclude(conveyor_camera=camera)
                .exists()
            ):
                raise TransportCameraConflict(
                    {
                        "detail": "Эта камера номера уже привязана к другому конвейеру",
                        "code": "number_camera_in_use",
                    }
                )
            previous = _payload(camera, binding)
            try:
                with transaction.atomic():
                    binding, _ = ShippingTransportCamera.objects.update_or_create(
                        conveyor_camera=camera,
                        defaults={
                            "number_camera": number_camera,
                            "recognition_model": recognition_model,
                            "loading_zone": loading_zone,
                            "updated_by": request.user,
                        },
                    )
            except IntegrityError as exc:
                raise TransportCameraConflict(
                    {
                        "detail": "Привязка камеры изменилась. Обновите настройки и повторите",
                        "code": "number_camera_in_use",
                    }
                ) from exc
            log_event(
                "camera_settings",
                "Камера номера конвейера настроена",
                user=request.user,
                payload={
                    "conveyor_camera": camera,
                    "number_camera": number_camera,
                    "recognition_model": recognition_model,
                    "loading_zone": loading_zone,
                    "previous_loading_zone": previous["loading_zone"],
                    "previous_number_camera": previous["number_camera"],
                    "previous_recognition_model": previous["recognition_model"],
                },
            )
        return Response(_payload(camera, binding))

    def delete(self, request, cam: str):
        with transaction.atomic():
            lock_camera_binding()
            camera = _conveyor(cam)
            binding = ShippingTransportCamera.objects.filter(
                conveyor_camera=camera
            ).first()
            if binding:
                assert_camera_has_no_active_work(camera)
                log_event(
                    "camera_settings",
                    "Камера номера конвейера отвязана",
                    user=request.user,
                    payload={
                        "conveyor_camera": camera,
                        "number_camera": binding.number_camera,
                        "recognition_model": binding.recognition_model,
                    },
                )
                binding.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShippingTransportRecognizeView(APIView):
    permission_classes: ClassVar[list] = [IsSuperUser]

    def post(self, request, cam: str):
        camera = _conveyor(cam)
        binding = ShippingTransportCamera.objects.filter(conveyor_camera=camera).first()
        if binding is None:
            raise TransportCameraConflict(
                {
                    "detail": "Сначала сохраните камеру и модель распознавания",
                    "code": "transport_camera_not_configured",
                }
            )
        try:
            frame = identity.capture_frame(binding.number_camera)
            if not frame:
                raise ai.AiUnavailable("Shipping camera frame unavailable")
            frame = identity.recognition_frame(frame, binding.loading_zone)
            number = None
            if binding.recognition_model == "vehicle_number":
                try:
                    number = identity.primary_number(frame, binding.recognition_model)
                except (ai.AiUnavailable, ai.AiError, HTTPException, OSError, ValueError, TypeError):
                    pass  # The manual check follows the same saved-frame fallback.
            if not number:
                if not settings.OPENAI_API_KEY:
                    raise ai.AiError(503, "OpenAI is not configured")
                number, model, _ = identity.gpt_number(
                    frame, recognition_model=binding.recognition_model,
                )
                if model != binding.recognition_model:
                    number = None
            number = identity.valid_number(number, binding.recognition_model) or None
        except ai.AiUnavailable:
            return Response(
                {
                    "detail": "Не удалось получить кадр или результат распознавания. Повторите проверку",
                    "code": "transport_recognition_unavailable",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ai.AiError as exc:
            return Response(
                {
                    "detail": "Сервис распознавания недоступен. Повторите проверку",
                    "code": "transport_recognition_unavailable",
                },
                status=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if exc.status == 503
                    else status.HTTP_502_BAD_GATEWAY
                ),
            )
        except (HTTPException, OSError, ValueError, TypeError, KeyError, Image.DecompressionBombError):
            return Response(
                {
                    "detail": "Не удалось получить кадр или результат распознавания. Повторите проверку",
                    "code": "transport_recognition_unavailable",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Do not report a check against an old source/model after another
        # administrator changed or deleted this binding while inference ran.
        if not ShippingTransportCamera.objects.filter(
            pk=binding.pk, updated_at=binding.updated_at
        ).exists():
            raise TransportCameraConflict(
                {
                    "detail": "Настройки изменились во время проверки. Обновите их и повторите",
                    "code": "transport_camera_changed",
                }
            )
        return Response(
            {
                **_payload(camera, binding),
                "number": number,
                "observed_at": timezone.now(),
            }
        )
