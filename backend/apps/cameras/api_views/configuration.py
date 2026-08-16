"""Camera inventory and monoblock configuration endpoints."""

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser
from apps.conveyors.models import ConveyorDevice
from apps.conveyors.services import (
    cloud_device_is_ready_for_session,
    control_payload,
    lock_camera_binding,
)
from apps.eventlog.services import log_event
from apps.orders.models import Order

from .. import services
from ..models import AiCountingSession, MonoblockCameraSettings, MonoblockDevice
from ..policies import active_device_for
from ..serializers import (
    CameraRenameSerializer,
    CameraSourcesSerializer,
    MonoblockDeviceCreateUpdateSerializer,
)


class CameraListView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("sys_permissions.manage")]

    def get(self, request):
        names = MonoblockCameraSettings.display_names()
        discovered = services.discover_cameras()
        sources = {
            source
            for camera in discovered
            if isinstance((source := camera.get("src")), str)
        }
        cloud_devices = {
            device.camera_source: device
            for device in ConveyorDevice.objects.filter(
                camera_source__in=sources,
                is_active=True,
            )
        }
        cameras = []
        for camera in discovered:
            source = camera.get("src")
            payload = {
                **camera,
                "zone": (
                    names.get(source, camera.get("zone"))
                    if isinstance(source, str)
                    else camera.get("zone")
                ),
            }
            cloud_device = cloud_devices.get(source)
            # Mirror the exact backend transport decision.  A stale enrolled
            # ESP stays terminally OFF and the camera remains available in
            # AI-only/manual mode; a fresh safe-OFF controller is advertised
            # as automatic cloud control.
            if cloud_device is not None:
                payload["conveyor"] = (
                    control_payload(cloud_device)
                    if cloud_device_is_ready_for_session(cloud_device)
                    else None
                )
            cameras.append(payload)
        device = active_device_for(request.user)
        if device is not None:
            cameras = [
                camera
                for camera in cameras
                if camera.get("src") == device.camera_source
            ]
        return Response(cameras)

    def patch(self, request):
        serializer = CameraRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        camera = serializer.validated_data["camera"]
        name = serializer.validated_data["name"]

        row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
        names = row.camera_names if isinstance(row.camera_names, dict) else {}
        row.camera_names = {**names, camera: name}
        row.updated_by = request.user
        row.save(update_fields=["camera_names", "updated_by", "updated_at"])
        return Response({"camera": camera, "name": name})


class MonoblockCameraSettingsView(APIView):
    """Shared allowlist for the camera dropdown in the Monoblock screen."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("shipping.load", "sys_permissions.manage")]
        return [HasPerm("sys_permissions.manage")]

    @staticmethod
    def _payload(settings_row=None, device=None):
        row = (
            settings_row
            or MonoblockCameraSettings.objects.filter(singleton=True).first()
        )
        if device is not None:
            return {
                "camera_sources": [device.camera_source],
                "locked": True,
                "device_id": device.pk,
                "device_name": device.name,
                "updated_at": device.updated_at,
            }
        return {
            "camera_sources": row.camera_sources if row else [],
            "locked": False,
            "device_id": None,
            "device_name": None,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload(device=active_device_for(request.user)))

    def put(self, request):
        serializer = CameraSourcesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sources = serializer.validated_data["camera_sources"]
        row, _ = MonoblockCameraSettings.objects.update_or_create(
            singleton=True,
            defaults={"camera_sources": sources, "updated_by": request.user},
        )
        return Response(self._payload(row))


def _device_payload(device, names=None):
    # names передаётся списком: иначе справочник подписей читается заново на
    # каждую строку ответа.
    if names is None:
        names = MonoblockCameraSettings.display_names()
    return {
        "id": device.pk,
        "name": device.name,
        "username": device.user.username,
        "camera_source": device.camera_source,
        "camera_name": names.get(device.camera_source, device.camera_source),
        "is_active": device.is_active,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }


def _unique_device_validation(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    constraint = str(getattr(diagnostic, "constraint_name", "") or "").lower()
    detail = f"{constraint} {exc}".lower()
    if "camera_source" in detail:
        return ValidationError(
            {
                "detail": "Камера уже закреплена за другим моноблоком",
                "code": "camera_busy",
            }
        )
    if "username" in detail:
        return ValidationError(
            {"detail": "Такой логин уже используется", "code": "username_busy"}
        )
    return None


def _assert_device_can_change_binding(
    device: MonoblockDevice,
    *,
    camera: str,
    is_active: bool,
) -> None:
    """Do not strand an open loading by moving or disabling its device."""
    binding_changes = camera != device.camera_source
    deactivates = device.is_active and not is_active
    if not binding_changes and not deactivates:
        return

    affected_cameras = {device.camera_source}
    if binding_changes:
        affected_cameras.add(camera)
    if AiCountingSession.objects.filter(
        camera__in=affected_cameras,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists() or Order.objects.filter(
        loading_camera__in=affected_cameras,
        status__in=("confirmed", "arrived", "loading"),
    ).exists():
        raise ValidationError(
            {
                "detail": "Сначала завершите активную отгрузку этого моноблока",
                "code": "monoblock_busy",
            }
        )


def _assert_camera_has_no_active_work(camera: str) -> None:
    if AiCountingSession.objects.filter(
        camera=camera,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists() or Order.objects.filter(
        loading_camera=camera,
        status__in=("confirmed", "arrived", "loading"),
    ).exists():
        raise ValidationError({
            "detail": "Сначала завершите активную отгрузку этой камеры",
            "code": "monoblock_busy",
        })


class MonoblockDeviceListView(APIView):
    """Суперпользователь создаёт отдельные аккаунты физических устройств."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request):
        devices = MonoblockDevice.objects.select_related("user").all()
        names = MonoblockCameraSettings.display_names()
        return Response([_device_payload(device, names) for device in devices])

    def post(self, request):
        serializer = MonoblockDeviceCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        name = data["name"]
        username = data["username"]
        camera = data["camera_source"]
        is_active = data["is_active"]

        User = get_user_model()
        try:
            with transaction.atomic():
                # Common order is camera mutex -> MonoblockDevice/User. AI
                # reservation takes the same mutex before creating OPEN.
                lock_camera_binding()
                if is_active:
                    _assert_camera_has_no_active_work(camera)
                user = User.objects.create_user(
                    username=username,
                    password=data["password"],
                    is_client=False,
                    is_active=is_active,
                )
                device = MonoblockDevice.objects.create(
                    user=user,
                    name=name,
                    camera_source=camera,
                    is_active=is_active,
                    created_by=request.user,
                )
        except IntegrityError as exc:
            validation_error = _unique_device_validation(exc)
            if validation_error is None:
                raise
            raise validation_error from exc

        log_event(
            "monoblock_device",
            f"Создан моноблок «{name}»",
            user=request.user,
            payload={"device_id": device.pk, "username": username, "camera": camera},
        )
        return Response(_device_payload(device), status=status.HTTP_201_CREATED)


class MonoblockDeviceDetailView(APIView):
    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    @staticmethod
    def _get(pk, *, lock: bool = False):
        devices = MonoblockDevice.objects.select_related("user")
        if lock:
            devices = devices.select_for_update(of=("self",))
        return get_object_or_404(devices, pk=pk)

    def patch(self, request, pk):
        try:
            with transaction.atomic():
                lock_camera_binding()
                device = self._get(pk, lock=True)
                serializer = MonoblockDeviceCreateUpdateSerializer(
                    device,
                    data=request.data,
                )
                serializer.is_valid(raise_exception=True)
                data = serializer.validated_data
                name = data["name"]
                username = data["username"]
                camera = data["camera_source"]
                is_active = data.get("is_active", device.is_active)
                password = data.get("password")
                _assert_device_can_change_binding(
                    device,
                    camera=camera,
                    is_active=is_active,
                )

                before = {
                    "name": device.name,
                    "username": device.user.username,
                    "camera": device.camera_source,
                    "is_active": device.is_active,
                }
                device.name = name
                device.camera_source = camera
                device.is_active = is_active
                device.save(
                    update_fields=["name", "camera_source", "is_active", "updated_at"]
                )

                device.user.username = username
                device.user.is_active = is_active
                user_update_fields = ["username", "is_active"]
                if password:
                    device.user.set_password(password)
                    user_update_fields.append("password")
                device.user.save(update_fields=user_update_fields)
        except IntegrityError as exc:
            validation_error = _unique_device_validation(exc)
            if validation_error is None:
                raise
            raise validation_error from exc

        log_event(
            "monoblock_device",
            f"Изменён моноблок «{name}»",
            user=request.user,
            payload={
                "device_id": device.pk,
                "before": before,
                "after": {
                    "name": name,
                    "username": username,
                    "camera": camera,
                    "is_active": device.is_active,
                },
            },
        )
        return Response(_device_payload(device))

    put = patch

    def delete(self, request, pk):
        with transaction.atomic():
            lock_camera_binding()
            device = self._get(pk, lock=True)
            if AiCountingSession.objects.filter(
                camera=device.camera_source,
                status__in=AiCountingSession.OPEN_STATUSES,
            ).exists() or Order.objects.filter(
                loading_camera=device.camera_source,
                status__in=("confirmed", "arrived", "loading"),
            ).exists():
                raise ValidationError(
                    {
                        "detail": (
                            "Сначала завершите активную отгрузку этого моноблока"
                        ),
                        "code": "monoblock_busy",
                    }
                )
            snapshot = _device_payload(device)
            name = device.name
            device.user.delete()

        log_event(
            "monoblock_device",
            f"Удалён моноблок «{name}»",
            user=request.user,
            payload={
                "device_id": snapshot["id"],
                "name": snapshot["name"],
                "username": snapshot["username"],
                "camera": snapshot["camera_source"],
            },
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
