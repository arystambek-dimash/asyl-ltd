from django.db.models import Prefetch
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import keyset_page
from apps.common.permissions import PermAPIViewMixin
from apps.common.viewsets import NoStoreMixin

from .models import AutomaticPassageCapture, UnassignedWeighing, WeighingPhotoDelivery
from .photos import KIND_EVIDENCE, photo_url


class PassageScaleHistoryView(NoStoreMixin, PermAPIViewMixin, APIView):
    required_perms = {"get": "grain.view"}

    def get(self, request):
        rows = (
            AutomaticPassageCapture.objects.select_related("wagon", "unassigned_weighing")
            .prefetch_related(
                # jobs[-1] ниже — последняя доставка захвата, порядок только явный.
                Prefetch("photo_deliveries", queryset=WeighingPhotoDelivery.objects.order_by("id"))
            )
            .order_by("-id")
        )
        page, next_cursor = keyset_page(rows, request.query_params.get("before"))
        results = []
        for capture in page:
            jobs = list(capture.photo_deliveries.all())
            photo = next((job for job in jobs if job.photo), None)
            item = getattr(capture, "unassigned_weighing", None)
            results.append(
                {
                    "id": capture.pk,
                    "occurred_at": capture.stable_weight_at or capture.started_at,
                    "weight_kg": capture.weight_kg,
                    "vehicle_number": (item.vehicle_number if item else "") or capture.vehicle_number,
                    "status": capture.status,
                    "action": capture.action,
                    "reason": (item.reason if item else "") or capture.error_code,
                    "detail": capture.error_detail,
                    "wagon_id": capture.wagon_id or (item.wagon_id if item else None),
                    "resolved": bool(item and item.status != UnassignedWeighing.OPEN),
                    "resolution": (
                        "discarded" if item and item.status == UnassignedWeighing.DISCARDED else
                        ("manual" if item.resolved_by_id else "automatic") if item and item.status == UnassignedWeighing.ASSIGNED else None
                    ),
                    "photo_url": photo_url(KIND_EVIDENCE, photo),
                    "photo_status": (
                        "saved"
                        if photo
                        else (jobs[-1].status if jobs else "unavailable")
                    ),
                }
            )
        return Response({"results": results, "next_cursor": next_cursor})
