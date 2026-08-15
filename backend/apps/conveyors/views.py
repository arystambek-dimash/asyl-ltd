from typing import ClassVar

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser
from apps.eventlog.services import log_event
from apps.orders.models import Order

from .authentication import AiCallbackAuthentication, ConveyorDeviceAuthentication
from .credentials import authorization_value, digest_token, generate_token
from .models import ConveyorDevice
from .permissions import IsAiCallback, IsConveyorDevice
from .serializers import (
    AiObservationSerializer,
    ConveyorDeviceEnrollSerializer,
    ConveyorDeviceUpdateSerializer,
    DeviceSyncSerializer,
    device_payload,
)
from .services import (
    ConveyorDeviceError,
    disable_device,
    emergency_stop,
    record_ai_observation,
    rotate_secret,
    sync_device,
    update_device,
)
from .throttles import ConveyorAiRateThrottle, ConveyorDeviceRateThrottle


def _service_error(exc: ConveyorDeviceError) -> Response:
    return Response(
        {"detail": exc.detail, "code": exc.code},
        status=exc.status,
    )


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    return response


def _log_automatic_stop(audit: dict | None) -> None:
    if audit is None:
        return
    order = None
    session_id = audit.get("session_id")
    if session_id is not None:
        order = Order.objects.filter(ai_counting_sessions__pk=session_id).first()
    log_event(
        "conveyor_auto_stop",
        f"Автоматическая остановка {audit['camera']}: {audit['reason']}",
        order=order,
        payload=audit,
    )


class NoStoreAPIView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class ConveyorDeviceSyncView(NoStoreAPIView):
    authentication_classes: ClassVar[list[type]] = [ConveyorDeviceAuthentication]
    permission_classes: ClassVar[list[type]] = [IsConveyorDevice]
    throttle_classes: ClassVar[list[type]] = [ConveyorDeviceRateThrottle]

    def post(self, request):
        serializer = DeviceSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = sync_device(request.auth.pk, serializer.validated_data)
        except ConveyorDeviceError as exc:
            return _no_store(_service_error(exc))
        _log_automatic_stop(result.audit)
        return _no_store(Response(result.payload))


class ConveyorAiObservationView(NoStoreAPIView):
    authentication_classes: ClassVar[list[type]] = [AiCallbackAuthentication]
    permission_classes: ClassVar[list[type]] = [IsAiCallback]
    throttle_classes: ClassVar[list[type]] = [ConveyorAiRateThrottle]

    def post(self, request):
        serializer = AiObservationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = record_ai_observation(serializer.validated_data)
        except ConveyorDeviceError as exc:
            return _no_store(_service_error(exc))
        _log_automatic_stop(result.audit)
        return _no_store(Response(result.payload))


def _credential_payload(device: ConveyorDevice, token: str) -> dict:
    return {
        "device_id": str(device.public_id),
        "token": token,
        "authorization": authorization_value(device.public_id, token),
    }


class ConveyorDeviceListView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request):
        return Response(
            [device_payload(device) for device in ConveyorDevice.objects.all()]
        )

    def post(self, request):
        serializer = ConveyorDeviceEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = generate_token()
        try:
            device = ConveyorDevice.objects.create(
                **serializer.validated_data,
                secret_sha256=digest_token(token),
                created_by=request.user,
            )
        except IntegrityError as exc:
            raise ValidationError(
                {
                    "detail": "A conveyor device is already assigned to this camera",
                    "code": "camera_busy",
                }
            ) from exc
        log_event(
            "conveyor_device_enrolled",
            f"Зарегистрирован ESP32 «{device.name}» ({device.camera_source})",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        payload = device_payload(device)
        # This is the only response that reveals the initial bearer.
        payload["credential"] = _credential_payload(device, token)
        return _no_store(Response(payload, status=status.HTTP_201_CREATED))


class ConveyorDeviceDetailView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    @staticmethod
    def _get(public_id):
        return get_object_or_404(ConveyorDevice, public_id=public_id)

    def get(self, request, public_id):
        return Response(device_payload(self._get(public_id)))

    def patch(self, request, public_id):
        serializer = ConveyorDeviceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device = update_device(
                self._get(public_id), serializer.validated_data,
            )
        except ConveyorDeviceError as exc:
            return _service_error(exc)
        except IntegrityError as exc:
            raise ValidationError(
                {
                    "detail": "A conveyor device is already assigned to this camera",
                    "code": "camera_busy",
                }
            ) from exc
        log_event(
            "conveyor_device_updated",
            f"Изменён ESP32 «{device.name}»",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        return Response(device_payload(device))

    put = patch

    def delete(self, request, public_id):
        device = disable_device(self._get(public_id))
        log_event(
            "conveyor_device_disabled",
            f"Отключён ESP32 «{device.name}»",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConveyorDeviceRotateSecretView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def post(self, request, public_id):
        device, token = rotate_secret(
            get_object_or_404(ConveyorDevice, public_id=public_id)
        )
        log_event(
            "conveyor_device_secret_rotated",
            f"Секрет ESP32 «{device.name}» заменён; конвейер остановлен",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        return _no_store(Response(
            {
                **device_payload(device),
                "credential": _credential_payload(device, token),
            }
        ))


class ConveyorDeviceDisableView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def post(self, request, public_id):
        device = disable_device(
            get_object_or_404(ConveyorDevice, public_id=public_id)
        )
        log_event(
            "conveyor_device_disabled",
            f"Отключён ESP32 «{device.name}»",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        return Response(device_payload(device))


class ConveyorDeviceEmergencyStopView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def post(self, request, public_id):
        device = emergency_stop(
            get_object_or_404(ConveyorDevice, public_id=public_id)
        )
        log_event(
            "conveyor_emergency_stop",
            f"Аварийно остановлен ESP32 «{device.name}»",
            user=request.user,
            payload={"device_id": str(device.public_id), "camera": device.camera_source},
        )
        return Response(device_payload(device))
