"""Read-only automation state and evidence in monoblock order details."""

from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm
from apps.common.signed_media import SignedMediaView
from apps.orders.models import Order
from apps.sales.access import scope_by_client_department

from .. import ai, shipping_automation, shipping_completion
from ..models import (
    AiCountingSession,
    MonoblockCameraSettings,
    ShippingTransportCamera,
    ShippingTransportRecognitionEvent,
    ShippingTransportState,
)
from ..shipping_tracking import current_tracking, unknown_tracking

PERMISSIONS = ("shipping.view", "shipping.load", "train.view", "train.load")
IMAGE_SALT = "shipping-transport-evidence-v1"


def _visible_evidence(user):
    visible = scope_by_client_department(
        Order.objects.all(), user, client_path="client"
    )
    return ShippingTransportRecognitionEvent.objects.filter(
        Q(order_id__isnull=True, status__in=("no_order", "multiple_orders", "observed"))
        | Q(order__in=visible)
    )


class ShippingTransportStatusView(APIView):
    def get_permissions(self):
        return [HasPerm(*PERMISSIONS)]

    def get(self, request):
        bindings = {
            row.conveyor_camera: row for row in ShippingTransportCamera.objects.all()
        }
        states = {
            row.conveyor_camera: row
            for row in ShippingTransportState.objects.select_related("session__order")
        }
        live = {
            row.camera: row
            for row in AiCountingSession.objects.filter(
                status__in=AiCountingSession.OPEN_STATUSES
            ).select_related("order")
        }
        visible_ids = set(
            scope_by_client_department(
                Order.objects.filter(
                    pk__in=[row.order_id for row in live.values()]
                    + [
                        row.session.order_id
                        for row in states.values()
                        if row.session_id
                    ]
                ),
                request.user,
                client_path="client",
            ).values_list("id", flat=True)
        )
        data = []
        for camera in sorted(MonoblockCameraSettings.shipping_sources()):
            binding = bindings.get(camera)
            state = states.get(camera)
            session = live.get(camera) or (
                state.session if state and state.session_id else None
            )
            configured = binding is not None
            current = bool(
                state
                and binding
                and state.binding_id == binding.pk
                and state.configuration_updated_at == binding.updated_at
            )
            phase = state.state if current else "waiting_number"
            detail = (
                state.detail
                if current
                else (
                    "Ожидаем автоматическое распознавание"
                    if configured
                    else "Камера номера не настроена"
                )
            )
            if current and (
                not state.polled_at
                or timezone.now() - state.polled_at > shipping_automation.MAX_AGE * 2
            ):
                phase, detail = (
                    "error",
                    "Нет свежих данных автоматического распознавания",
                )
            visible = session is None or session.order_id in visible_ids
            same_owner = bool(
                not session or (state and state.session_id == session.pk)
            )
            state_visible = bool(
                visible and (not state or not state.session_id or state.session.order_id in visible_ids)
            )
            expose_state = current and state_visible and same_owner
            if not visible or not same_owner or not state_visible:
                phase, detail = "busy", "Конвейер занят другой погрузкой"
            data.append(
                {
                    "conveyor_camera": camera,
                    "number_camera": binding.number_camera if binding else None,
                    "recognition_model": binding.recognition_model if binding else None,
                    "state": phase,
                    "detail": detail,
                    "number": (state.number or state.candidate_number or None)
                    if expose_state
                    else None,
                    "observed_at": state.observed_at if expose_state else None,
                    "order_id": session.order_id if session and visible else None,
                    "session_id": session.pk if session and visible else None,
                    "tracking": current_tracking(state.tracking)
                    if expose_state
                    else unknown_tracking(),
                    "tracking_alert": state.tracking_alert
                    if expose_state
                    else "",
                    "auto_finish": shipping_completion.public_status(state.auto_finish)
                    if expose_state
                    else shipping_completion.public_status(None),
                }
            )
        return Response(data)


class ShippingTransportEvidenceView(APIView):
    def get_permissions(self):
        return [HasPerm(*PERMISSIONS)]

    def get(self, request):
        rows = _visible_evidence(request.user)
        order_id = request.query_params.get("order_id")
        camera = request.query_params.get("conveyor_camera")
        if order_id:
            try:
                number = int(order_id)
                if number <= 0:
                    raise ValueError
            except ValueError as exc:
                raise ValidationError("Некорректный заказ") from exc
            rows = rows.filter(order_id=number)
        if camera:
            if not ai.CAM_RE.fullmatch(camera):
                raise ValidationError("Некорректная камера")
            rows = rows.filter(conveyor_camera=camera)
        rows = list(rows.select_related("session").order_by("-first_seen_at", "-id")[:100])
        alerts = dict(
            ShippingTransportState.objects.filter(
                session_id__in=[row.session_id for row in rows if row.session_id]
            ).values_list("session_id", "tracking_alert")
        )
        finish_states = dict(
            ShippingTransportState.objects.filter(
                session_id__in=[row.session_id for row in rows if row.session_id]
            ).values_list("session_id", "auto_finish")
        )
        data = []
        for row in rows:
            token = signing.dumps(
                {"id": row.pk, "user_id": request.user.pk}, salt=IMAGE_SALT
            )
            data.append(
                {
                    "id": row.pk,
                    "conveyor_camera": row.conveyor_camera,
                    "number_camera": row.number_camera,
                    "recognition_model": row.recognition_model,
                    "number": row.number,
                    "first_seen_at": row.first_seen_at,
                    "last_seen_at": row.last_seen_at,
                    "status": row.status,
                    "order_id": row.order_id,
                    "session_id": row.session_id,
                    "visit_id": row.visit_id,
                    # Evidence remains a historical observation; stale live
                    # state must not rewrite a completed order's snapshot.
                    "tracking": row.tracking or unknown_tracking(),
                    "tracking_alert": alerts.get(row.session_id, ""),
                    "auto_finish": shipping_completion.public_status(
                        row.session.last_status.get("auto_finish")
                        if row.session_id and row.session.status == AiCountingSession.CLOSED
                        else finish_states.get(row.session_id),
                        historical=bool(row.session_id and row.session.status == AiCountingSession.CLOSED),
                    ),
                    "image_url": request.build_absolute_uri(
                        f"/api/cameras/shipping-transport/history/{row.pk}/image/?token={token}"
                    )
                    if row.image
                    else None,
                }
            )
        return Response(data)


class ShippingTransportEvidenceImageView(SignedMediaView):
    """The signed URL still rechecks the original user's activity, permissions and scope."""

    def get(self, request, pk: int):
        try:
            payload = signing.loads(
                request.query_params.get("token", ""), salt=IMAGE_SALT, max_age=600
            )
        except signing.BadSignature as exc:
            raise Http404 from exc
        if not isinstance(payload, dict) or payload.get("id") != pk:
            raise Http404
        user = (
            get_user_model()
            .objects.filter(pk=payload.get("user_id"), is_active=True, is_client=False)
            .first()
        )
        if user is None or not any(user.has_perm_code(code) for code in PERMISSIONS):
            raise Http404
        row = get_object_or_404(_visible_evidence(user), pk=pk)
        if not row.image:
            raise Http404
        try:
            stream = row.image.open("rb")
        except OSError as exc:
            raise Http404 from exc
        response = FileResponse(stream, content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
