"""Camera inventory and monoblock configuration endpoints."""

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff
from apps.orders.models import Order

from .. import ai, continuous, services
from ..models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    MonoblockCameraSettings,
)
from ..policies import (
    assert_no_pending_shipping_bootstrap,
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

        row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
        names = row.camera_names if isinstance(row.camera_names, dict) else {}
        row.camera_names = {**names, camera: name}
        row.updated_by = request.user
        row.save(update_fields=["camera_names", "updated_by", "updated_at"])
        return Response({"camera": camera, "name": name})


def _camera_role_conflict(cameras, occupied, *, owner: str) -> None:
    conflicts = sorted(set(cameras) & set(occupied))
    if conflicts:
        raise ValidationError(
            {
                "camera_sources": (
                    "Одна камера может иметь только один контур подсчёта. "
                    f"Уже используется в {owner}: " + ", ".join(conflicts)
                ),
                "code": "camera_role_conflict",
                "cameras": conflicts,
            }
        )


def _sync_effective_always_on(previous_sources: list[str]) -> tuple[str, str]:
    """Best-effort immediate apply; PostgreSQL remains the durable authority."""

    if not ai.enabled():
        return "pending", "AI-сервис не настроен; выбор применится после настройки"
    try:
        live = continuous.sync_always_on_policy(previous_sources=previous_sources)
    except (ai.AiUnavailable, ai.AiError) as exc:
        return "pending", str(exc)
    return continuous.contour_sync_state(
        live,
        MonoblockCameraSettings.shipping_sources(),
        ANALYTICS_SCOPE_SHIPPING,
    )


def _assert_known_always_on_capacity(
    effective_sources: list[str],
    *,
    previous_sources: list[str] | None = None,
) -> None:
    """Reject an impossible policy when a trustworthy cached limit is known."""

    # A reduction (or a camera swap at the same cardinality) is how an
    # already-over-capacity installation recovers. Never block that path.
    if previous_sources is not None and len(effective_sources) <= len(
        previous_sources
    ):
        return
    live = ai.cached_always_on_status() if ai.enabled() else None
    capacity = (live or {}).get("capacity")
    if (
        type(capacity) is int
        and capacity >= 0
        and len(effective_sources) > capacity
    ):
        raise ValidationError(
            {
                "camera_sources": (
                    f"ПК камер поддерживает до {capacity} активных процессоров"
                ),
                "code": "always_on_capacity_exceeded",
            }
        )


class MonoblockCameraSettingsView(APIView):
    """Shared allowlist for the camera dropdown in the Monoblock screen."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("shipping.load", "sys_permissions.manage")]
        return [HasPerm("sys_permissions.manage")]

    @staticmethod
    def _payload(
        settings_row=None,
        *,
        always_on_sync_status="synced",
        always_on_detail="",
        live=None,
    ):
        row = (
            settings_row
            or MonoblockCameraSettings.objects.filter(singleton=True).first()
        )
        payload = {
            "camera_sources": row.camera_sources if row else [],
            "updated_at": row.updated_at if row else None,
        }
        visible_sources = MonoblockCameraSettings.shipping_sources(row)
        readiness = continuous.contour_readiness(
            live or {},
            visible_sources,
            ANALYTICS_SCOPE_SHIPPING,
        )
        live_scopes = (live or {}).get("analytics_scopes")
        if not isinstance(live_scopes, dict):
            live_scopes = {}
        processors = [
            processor
            for processor in (live or {}).get("processors", [])
            if isinstance(processor, dict)
            and processor.get("cam") in set(visible_sources)
            and processor.get(
                "analytics_scope",
                live_scopes.get(processor.get("cam")),
            )
            == ANALYTICS_SCOPE_SHIPPING
        ]
        return {
            **payload,
            # Compatibility aliases consumed by the currently deployed UI.
            "always_on_camera_sources": visible_sources,
            "always_on_source": "sub",
            "always_on_sync_status": always_on_sync_status,
            "always_on_detail": always_on_detail,
            "continuous_camera_sources": visible_sources,
            "continuous_source": "sub",
            "continuous_sync_status": always_on_sync_status,
            "continuous_detail": always_on_detail,
            "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            "blocked_camera_sources": MonoblockCameraSettings.reserved_sources(
                ANALYTICS_SCOPE_AI247
            ),
            "active_other_camera_sources": MonoblockCameraSettings.ai247_sources(row),
            "camera_readiness": readiness,
            "processors": processors,
        }

    def get(self, request):
        live = None
        if not ai.enabled():
            return Response(
                self._payload(
                    always_on_sync_status="pending",
                    always_on_detail="AI-сервис не настроен",
                    live=None,
                )
            )
        try:
            live = ai.always_on_status_cached()
            desired = MonoblockCameraSettings.shipping_sources()
            sync_status, detail = continuous.contour_sync_state(
                live,
                desired,
                ANALYTICS_SCOPE_SHIPPING,
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            sync_status, detail = "pending", str(exc)
        return Response(
            self._payload(
                always_on_sync_status=sync_status,
                always_on_detail=detail,
                live=live,
            )
        )

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
            proposed_shipping = MonoblockCameraSettings._ordered_camera_union(sources)
            assert_no_pending_shipping_bootstrap(
                set(previous_shipping) - set(proposed_shipping)
            )
            changed_sources = set(previous_shipping) ^ set(proposed_shipping)
            for camera in sorted(changed_sources):
                _assert_camera_has_no_active_work(camera)
            ai247_sources = MonoblockCameraSettings.ai247_sources(row)
            reserve_camera_roles(ai247_sources, ANALYTICS_SCOPE_AI247)
            reserve_camera_roles(proposed_shipping, ANALYTICS_SCOPE_SHIPPING)
            _camera_role_conflict(
                proposed_shipping,
                ai247_sources,
                owner="AI 24/7",
            )
            effective_sources = MonoblockCameraSettings._ordered_camera_union(
                proposed_shipping,
                row.always_on_camera_sources,
            )
            _assert_known_always_on_capacity(
                effective_sources,
                previous_sources=previous_sources,
            )
            row.camera_sources = sources
            row.updated_by = request.user
            row.save(update_fields=["camera_sources", "updated_by", "updated_at"])
        sync_status, detail = _sync_effective_always_on(previous_sources)
        row = MonoblockCameraSettings.objects.get(singleton=True)
        return Response(
            self._payload(
                row,
                always_on_sync_status=sync_status,
                always_on_detail=detail,
                live=ai.cached_always_on_status(),
            ),
            status=(
                status.HTTP_200_OK
                if sync_status == "synced"
                else status.HTTP_202_ACCEPTED
            ),
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
