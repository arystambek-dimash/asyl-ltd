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
from apps.eventlog.services import log_event
from apps.orders.models import Order

from .. import ai, continuous, services
from ..models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    MonoblockCameraSettings,
    MonoblockDevice,
)
from ..policies import (
    active_device_for,
    assert_no_pending_shipping_bootstrap,
    reserve_camera_roles,
)
from ..serializers import (
    CameraRenameSerializer,
    CameraSourcesSerializer,
    MonoblockDeviceCreateUpdateSerializer,
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


def _sync_changed_device_policy(previous_sources: list[str]) -> tuple[str, str]:
    """Avoid a remote write when a device edit did not change AI membership."""

    if previous_sources == MonoblockCameraSettings.continuous_sources():
        return "synced", ""
    return _sync_effective_always_on(previous_sources)


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
        device=None,
        *,
        always_on_sync_status="synced",
        always_on_detail="",
        live=None,
    ):
        row = (
            settings_row
            or MonoblockCameraSettings.objects.filter(singleton=True).first()
        )
        if device is not None:
            payload = {
                "camera_sources": [device.camera_source],
                "locked": True,
                "device_id": device.pk,
                "device_name": device.name,
                "updated_at": device.updated_at,
            }
        else:
            payload = {
                "camera_sources": row.camera_sources if row else [],
                "locked": False,
                "device_id": None,
                "device_name": None,
                "updated_at": row.updated_at if row else None,
            }
        visible_sources = (
            [device.camera_source]
            if device is not None
            else MonoblockCameraSettings.shipping_sources(row)
        )
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
        device = active_device_for(request.user)
        live = None
        if not ai.enabled():
            return Response(
                self._payload(
                    device=device,
                    always_on_sync_status="pending",
                    always_on_detail="AI-сервис не настроен",
                    live=None,
                )
            )
        try:
            live = ai.always_on_status_cached()
            desired = (
                [device.camera_source]
                if device is not None
                else MonoblockCameraSettings.shipping_sources()
            )
            sync_status, detail = continuous.contour_sync_state(
                live,
                desired,
                ANALYTICS_SCOPE_SHIPPING,
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            sync_status, detail = "pending", str(exc)
        return Response(
            self._payload(
                device=device,
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
            device_sources = MonoblockDevice.objects.filter(
                is_active=True
            ).values_list("camera_source", flat=True)
            proposed_shipping = MonoblockCameraSettings._ordered_camera_union(
                sources,
                device_sources,
            )
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


def _device_payload(
    device,
    names=None,
    *,
    always_on_sync_status=None,
    always_on_detail="",
):
    # names передаётся списком: иначе справочник подписей читается заново на
    # каждую строку ответа.
    if names is None:
        names = MonoblockCameraSettings.display_names()
    payload = {
        "id": device.pk,
        "name": device.name,
        "username": device.user.username,
        "camera_source": device.camera_source,
        "camera_name": names.get(device.camera_source, device.camera_source),
        "is_active": device.is_active,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }
    if always_on_sync_status is not None:
        payload.update(
            always_on_source="sub",
            always_on_sync_status=always_on_sync_status,
            always_on_detail=always_on_detail,
        )
    return payload


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
    activates = not device.is_active and is_active
    if not binding_changes and not deactivates and not activates:
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
                # AI reservations take the same mutex before creating OPEN.
                lock_camera_binding()
                previous_sources = MonoblockCameraSettings.continuous_sources()
                reserve_camera_roles(
                    MonoblockCameraSettings.shipping_sources(),
                    ANALYTICS_SCOPE_SHIPPING,
                )
                reserve_camera_roles(
                    MonoblockCameraSettings.ai247_sources(),
                    ANALYTICS_SCOPE_AI247,
                )
                reserve_camera_roles([camera], ANALYTICS_SCOPE_SHIPPING)
                if is_active:
                    _assert_camera_has_no_active_work(camera)
                    _camera_role_conflict(
                        [camera],
                        MonoblockCameraSettings.ai247_sources(),
                        owner="AI 24/7",
                    )
                    _assert_known_always_on_capacity(
                        MonoblockCameraSettings._ordered_camera_union(
                            previous_sources,
                            [camera],
                        ),
                        previous_sources=previous_sources,
                    )
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
        sync_status, detail = _sync_changed_device_policy(previous_sources)
        return Response(
            _device_payload(
                device,
                always_on_sync_status=sync_status,
                always_on_detail=detail,
            ),
            status=(
                status.HTTP_201_CREATED
                if sync_status == "synced"
                else status.HTTP_202_ACCEPTED
            ),
        )


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
                previous_sources = MonoblockCameraSettings.continuous_sources()
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
                row = MonoblockCameraSettings.objects.get(singleton=True)
                other_device_sources = MonoblockDevice.objects.filter(
                    is_active=True
                ).exclude(pk=device.pk).values_list("camera_source", flat=True)
                proposed_device_sources = list(other_device_sources)
                if is_active:
                    proposed_device_sources.append(camera)
                proposed_shipping = MonoblockCameraSettings._ordered_camera_union(
                    row.camera_sources,
                    proposed_device_sources,
                )
                assert_no_pending_shipping_bootstrap(
                    set(MonoblockCameraSettings.shipping_sources(row))
                    - set(proposed_shipping)
                )
                # Global mutation lock order is bootstrap marker -> immutable
                # role. The importer also reaches both tables; keeping this
                # order prevents a marker/role deadlock during cutover.
                reserve_camera_roles(
                    MonoblockCameraSettings.shipping_sources(row),
                    ANALYTICS_SCOPE_SHIPPING,
                )
                reserve_camera_roles(
                    MonoblockCameraSettings.ai247_sources(row),
                    ANALYTICS_SCOPE_AI247,
                )
                reserve_camera_roles([camera], ANALYTICS_SCOPE_SHIPPING)
                if is_active:
                    _camera_role_conflict(
                        [camera],
                        MonoblockCameraSettings.ai247_sources(row),
                        owner="AI 24/7",
                    )
                _assert_known_always_on_capacity(
                    MonoblockCameraSettings._ordered_camera_union(
                        row.camera_sources,
                        proposed_device_sources,
                        row.always_on_camera_sources,
                    ),
                    previous_sources=previous_sources,
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
        sync_status, detail = _sync_changed_device_policy(previous_sources)
        return Response(
            _device_payload(
                device,
                always_on_sync_status=sync_status,
                always_on_detail=detail,
            ),
            status=(
                status.HTTP_200_OK
                if sync_status == "synced"
                else status.HTTP_202_ACCEPTED
            ),
        )

    put = patch

    def delete(self, request, pk):
        with transaction.atomic():
            lock_camera_binding()
            device = self._get(pk, lock=True)
            previous_sources = MonoblockCameraSettings.continuous_sources()
            row = MonoblockCameraSettings.objects.get(singleton=True)
            proposed_shipping = MonoblockCameraSettings._ordered_camera_union(
                row.camera_sources,
                MonoblockDevice.objects.filter(is_active=True)
                .exclude(pk=device.pk)
                .values_list("camera_source", flat=True),
            )
            assert_no_pending_shipping_bootstrap(
                set(MonoblockCameraSettings.shipping_sources(row))
                - set(proposed_shipping)
            )
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
        sync_status, detail = _sync_changed_device_policy(previous_sources)
        if sync_status == "pending":
            return Response(
                {
                    "deleted": True,
                    "always_on_source": "sub",
                    "always_on_sync_status": sync_status,
                    "always_on_detail": detail,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
