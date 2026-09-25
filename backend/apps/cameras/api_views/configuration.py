"""Camera inventory and monoblock configuration endpoints."""

from typing import ClassVar

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, PermAPIViewMixin
from apps.eventlog.services import log_event

from .. import continuous, services
from ..models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    MonoblockCameraSettings,
    ShippingTransportCamera,
)
from ..policies import (
    MONOBLOCK_SETTINGS_VIEW,
    assert_always_on_capacity,
    assert_camera_has_no_active_work,
    assert_no_role_conflict,
    reserve_camera_roles,
)
from ..serializers import (
    CameraRenameSerializer,
    CameraSourcesSerializer,
)
from ..sessions import lock_camera_binding


class CameraListView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("sys_permissions.manage")]

    def get(self, request):
        names = MonoblockCameraSettings.display_names()
        cameras = []
        for camera in services.discover_cameras():
            source = camera.get("src")
            cameras.append(
                {
                    **camera,
                    "zone": (
                        names.get(source, camera.get("zone"))
                        if isinstance(source, str)
                        else camera.get("zone")
                    ),
                }
            )
        return Response(cameras)

    def patch(self, request):
        serializer = CameraRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        camera = serializer.validated_data["camera"]
        name = serializer.validated_data["name"]

        row = MonoblockCameraSettings.load()
        names = row.camera_names if isinstance(row.camera_names, dict) else {}
        row.camera_names = {**names, camera: name}
        row.updated_by = request.user
        row.save(update_fields=["camera_names", "updated_by", "updated_at"])
        return Response({"camera": camera, "name": name})


class MonoblockCameraSettingsView(PermAPIViewMixin, APIView):
    """Shared allowlist for the camera dropdown in the Monoblock screen."""

    required_perms: ClassVar[dict] = {
        "get": MONOBLOCK_SETTINGS_VIEW,
        "put": ("sys_permissions.manage",),
    }

    @staticmethod
    def _payload(row=None):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        return {
            "camera_sources": row.camera_sources if row else [],
            "blocked_camera_sources": MonoblockCameraSettings.reserved_sources(
                ANALYTICS_SCOPE_AI247
            ),
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        serializer = CameraSourcesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sources = serializer.validated_data["camera_sources"]
        with transaction.atomic():
            lock_camera_binding()
            row = MonoblockCameraSettings.objects.select_for_update().get(
                singleton=True
            )
            previous_sources = MonoblockCameraSettings.continuous_sources(row)
            previous_shipping = MonoblockCameraSettings.shipping_sources(row)
            proposed_shipping = MonoblockCameraSettings.ordered_camera_union(sources)
            changed_sources = set(previous_shipping) ^ set(proposed_shipping)
            for camera in sorted(changed_sources):
                assert_camera_has_no_active_work(camera)
            ai247_sources = MonoblockCameraSettings.ai247_sources(row)
            reserve_camera_roles(ai247_sources, ANALYTICS_SCOPE_AI247)
            reserve_camera_roles(proposed_shipping, ANALYTICS_SCOPE_SHIPPING)
            assert_no_role_conflict(
                proposed_shipping,
                ai247_sources,
                owner="AI 24/7",
            )
            effective_sources = MonoblockCameraSettings.ordered_camera_union(
                proposed_shipping,
                row.always_on_camera_sources,
            )
            assert_always_on_capacity(effective_sources, previous_sources)
            row.camera_sources = sources
            row.updated_by = request.user
            row.save(update_fields=["camera_sources", "updated_by", "updated_at"])
            # The association belongs to this conveyor assignment. Removing
            # a conveyor releases its number camera for another loading bay.
            removed_bindings = ShippingTransportCamera.objects.filter(
                conveyor_camera__in=set(previous_shipping) - set(proposed_shipping)
            )
            if not request.user.is_superuser and removed_bindings.exists():
                raise PermissionDenied(
                    "Только суперпользователь может удалить конвейер с привязанной камерой номера"
                )
            for binding in removed_bindings:
                log_event(
                    "camera_settings", "Камера номера отвязана вместе с конвейером",
                    user=request.user,
                    payload={
                        "conveyor_camera": binding.conveyor_camera,
                        "number_camera": binding.number_camera,
                        "recognition_model": binding.recognition_model,
                    },
                )
            removed_bindings.delete()
        _live, sync_status, _detail = continuous.apply_always_on_policy(
            previous_sources,
            ANALYTICS_SCOPE_SHIPPING,
        )
        return Response(
            self._payload(MonoblockCameraSettings.objects.get(singleton=True)),
            status=(
                status.HTTP_200_OK
                if sync_status == "synced"
                else status.HTTP_202_ACCEPTED
            ),
        )
