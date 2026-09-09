from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import PermAPIViewMixin

from .models import AutomaticPassageCapture
from .photos import photo_url


class PassageScaleHistoryView(PermAPIViewMixin, APIView):
    required_perms = {"get": "grain.view"}

    def get(self, request):
        rows = (
            AutomaticPassageCapture.objects.select_related(
                "wagon", "unassigned_weighing", "unassigned_weighing__identity_check"
            )
            .prefetch_related("photo_deliveries")
            .order_by("-id")
        )
        before = request.query_params.get("before")
        if before:
            try:
                cursor = int(before)
                if cursor <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValidationError("Некорректный номер страницы") from None
            rows = rows.filter(pk__lt=cursor)
        page = list(rows[:51])
        results = []
        for capture in page[:50]:
            jobs = list(capture.photo_deliveries.all())
            photo = next((job for job in jobs if job.photo), None)
            item = getattr(capture, "unassigned_weighing", None)
            from .weighing_identity import public_status
            results.append(
                {
                    "id": capture.pk,
                    "occurred_at": capture.stable_weight_at or capture.started_at,
                    "weight_kg": capture.weight_kg,
                    "observed_weight_kg": capture.trigger_weight_kg,
                    "vehicle_number": (item.vehicle_number if item else "") or capture.vehicle_number,
                    "orientation": capture.orientation,
                    "status": capture.status,
                    "stage": capture.stage,
                    "action": capture.action,
                    "reason": (item.reason if item else "") or capture.error_code,
                    "detail": capture.error_detail,
                    "wagon_id": capture.wagon_id or (item.wagon_id if item else None),
                    "unassigned_id": item.pk if item else None,
                    "resolved": bool(item and item.status != "open"),
                    "resolution": (
                        "discarded" if item and item.status == "discarded" else
                        ("manual" if item.resolved_by_id else "automatic") if item and item.status == "assigned" else None
                    ),
                    "identity_check": public_status(item) if item and item.status == "open" else None,
                    "photo_url": photo_url("evidence", photo),
                    "photo_status": (
                        "saved"
                        if photo
                        else (jobs[-1].status if jobs else "unavailable")
                    ),
                }
            )
        response = Response(
            {"results": results, "next_cursor": page[49].pk if len(page) > 50 else None}
        )
        response["Cache-Control"] = "no-store"
        return response
