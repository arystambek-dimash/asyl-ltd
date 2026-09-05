"""Self-collecting dataset for the Camera-PC front/rear orientation classifier.

Every weighing already leaves a frame and a truth about it: a completed trip
says which frame was the empty entry (truck facing the camera) and which the
loaded exit (truck showing its tail); a frame without a closed trip is still
labelled by its weight when that is unambiguous. This module turns those facts
into labelled samples and pushes them to Camera-PC, where a nightly job
retrains the classifier. Labels never come from the classifier itself, so the
loop cannot drift into confirming its own mistakes; a frame the classifier was
confidently wrong about is held back as a conflict for a human look.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.cameras import ai as camera_ai

from . import statuses as st
from .models import (
    VEHICLE_ORIENTATION_FRONT,
    VEHICLE_ORIENTATION_REAR,
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WeighingRecord,
)

log = logging.getLogger(__name__)

PHOTO_MISSING = "photo_missing"
# Где живёт исходный кадр: record_kind образца → модель взвешивания.
RECORD_MODELS = {
    VehicleOrientationSample.WEIGHING: WeighingRecord,
    VehicleOrientationSample.UNASSIGNED: UnassignedWeighing,
}


@dataclass(frozen=True, slots=True)
class Label:
    value: str
    source: str


def label_for_weight(weight_kg: int) -> Label | None:
    """Empty trucks are entries, loaded trucks exits; in between is unknown."""

    if weight_kg < settings.VEHICLE_ORIENTATION_EMPTY_MAX_KG:
        return Label(VEHICLE_ORIENTATION_FRONT, VehicleOrientationSample.BY_WEIGHT)
    if weight_kg > settings.VEHICLE_ORIENTATION_LOADED_MIN_KG:
        return Label(VEHICLE_ORIENTATION_REAR, VehicleOrientationSample.BY_WEIGHT)
    return None


def _trip_completed(wagon: Wagon | None) -> bool:
    return (
        wagon is not None
        and wagon.is_passage
        and wagon.status == st.COMPLETED
        and wagon.gross_weight_kg is not None
        and wagon.tare_weight_kg is not None
        and wagon.tare_weight_kg > wagon.gross_weight_kg
    )


def label_weighing(record: WeighingRecord) -> Label | None:
    wagon = record.wagon
    if not wagon.is_passage:
        return None
    if _trip_completed(wagon):
        return Label(
            VEHICLE_ORIENTATION_FRONT if record.kind == "gross" else VEHICLE_ORIENTATION_REAR,
            VehicleOrientationSample.BY_TRIP,
        )
    if wagon.status == st.CANCELLED:
        # A cancelled trip is usually a booking mistake; its weights prove nothing.
        return None
    return label_for_weight(record.weight_kg)


def label_unassigned(item: UnassignedWeighing) -> Label | None:
    if item.status == UnassignedWeighing.DISCARDED:
        return None
    if item.status == UnassignedWeighing.ASSIGNED and item.action and _trip_completed(item.wagon):
        return Label(
            VEHICLE_ORIENTATION_FRONT if item.action == "entry" else VEHICLE_ORIENTATION_REAR,
            VehicleOrientationSample.BY_TRIP,
        )
    return label_for_weight(item.weight_kg)


def _upsert(
    kind: str,
    record_id: int,
    label: Label,
    *,
    weight_kg: int,
    captured_at,
    model_orientation: str,
) -> str:
    """Create or refresh one sample row; returns created/updated/unchanged."""

    conflict = bool(model_orientation) and model_orientation != label.value
    sample, created = VehicleOrientationSample.objects.get_or_create(
        record_kind=kind,
        record_id=record_id,
        defaults={
            "label": label.value,
            "label_source": label.source,
            "weight_kg": weight_kg,
            "captured_at": captured_at,
            "model_orientation": model_orientation or "",
            "conflict": conflict,
        },
    )
    if created:
        return "created"
    if sample.label_source == VehicleOrientationSample.BY_MANUAL or sample.excluded:
        # A human decided; the automatic rules stay out of it.
        return "unchanged"
    if (
        sample.label == label.value
        and sample.label_source == label.source
        and sample.conflict == conflict
    ):
        return "unchanged"
    # A corrected trip changes the truth about this frame: send it again so
    # Camera-PC relabels the stored sample.
    sample.label = label.value
    sample.label_source = label.source
    sample.conflict = conflict
    sample.model_orientation = model_orientation or ""
    sample.sent_at = None
    sample.last_error = ""
    sample.save(
        update_fields=[
            "label",
            "label_source",
            "conflict",
            "model_orientation",
            "sent_at",
            "last_error",
            "updated_at",
        ]
    )
    return "updated"


def collect(*, limit: int | None = None) -> dict[str, int]:
    """Label every recent frame that has a photo; returns counters."""

    since = timezone.now() - timedelta(days=settings.VEHICLE_ORIENTATION_SAMPLE_MAX_AGE_DAYS)
    counters = {"created": 0, "updated": 0, "unchanged": 0, "unlabelled": 0}
    records = (
        WeighingRecord.objects.exclude(photo="")
        .filter(wagon__direction=Wagon.PASSAGE, created_at__gte=since)
        .select_related("wagon")
        .order_by("-id")
    )
    if limit is not None:
        records = records[:limit]
    for record in records:
        label = label_weighing(record)
        if label is None:
            counters["unlabelled"] += 1
            continue
        counters[
            _upsert(
                VehicleOrientationSample.WEIGHING,
                record.pk,
                label,
                weight_kg=record.weight_kg,
                captured_at=record.created_at,
                model_orientation=record.orientation,
            )
        ] += 1
    items = (
        UnassignedWeighing.objects.exclude(photo="")
        .filter(created_at__gte=since)
        .select_related("wagon")
        .order_by("-id")
    )
    if limit is not None:
        items = items[:limit]
    for item in items:
        label = label_unassigned(item)
        if label is None:
            counters["unlabelled"] += 1
            continue
        counters[
            _upsert(
                VehicleOrientationSample.UNASSIGNED,
                item.pk,
                label,
                weight_kg=item.weight_kg,
                captured_at=item.stable_weight_at,
                model_orientation=item.orientation,
            )
        ] += 1
    return counters


def load_records(samples) -> dict[tuple[str, int], WeighingRecord | UnassignedWeighing]:
    """Исходные взвешивания образцов одним запросом на вид — для фото и номера.

    Ключ — ``(record_kind, record_id)``; удалённой записи в словаре нет.
    """

    ids: dict[str, set[int]] = {}
    for sample in samples:
        ids.setdefault(sample.record_kind, set()).add(sample.record_id)
    records = {}
    for kind, pks in ids.items():
        rows = RECORD_MODELS[kind].objects.filter(pk__in=pks).select_related("wagon")
        records.update({(kind, row.pk): row for row in rows})
    return records


def _photo_bytes(sample: VehicleOrientationSample) -> bytes | None:
    model = RECORD_MODELS[sample.record_kind]
    record = model.objects.filter(pk=sample.record_id).exclude(photo="").first()
    if record is None:
        return None
    try:
        with record.photo.open("rb") as handle:
            data = handle.read()
    except (OSError, ValueError):
        return None
    return data or None


def set_manual_label(sample: VehicleOrientationSample, label: str, user) -> VehicleOrientationSample:
    """A reviewer says what the frame shows; Camera-PC gets the frame (again)."""

    if label not in {VEHICLE_ORIENTATION_FRONT, VEHICLE_ORIENTATION_REAR}:
        raise ValueError("label must be front or rear")
    sample.label = label
    sample.label_source = VehicleOrientationSample.BY_MANUAL
    sample.conflict = False
    sample.excluded = False
    sample.removal_pending = False
    sample.sent_at = None
    sample.last_error = ""
    sample.reviewed_by = user
    sample.reviewed_at = timezone.now()
    sample.save(
        update_fields=[
            "label",
            "label_source",
            "conflict",
            "excluded",
            "removal_pending",
            "sent_at",
            "last_error",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )
    return sample


def exclude_sample(sample: VehicleOrientationSample, user) -> VehicleOrientationSample:
    """Drop a frame from the dataset; a copy already on Camera-PC is removed."""

    sample.excluded = True
    sample.removal_pending = sample.sent_at is not None
    sample.conflict = False
    sample.reviewed_by = user
    sample.reviewed_at = timezone.now()
    sample.last_error = ""
    sample.save(
        update_fields=[
            "excluded",
            "removal_pending",
            "conflict",
            "reviewed_by",
            "reviewed_at",
            "last_error",
            "updated_at",
        ]
    )
    return sample


def export_removals(*, limit: int) -> dict[str, int]:
    """Ask Camera-PC to forget frames a reviewer excluded after they were sent."""

    counters = {"removed": 0, "remove_failed": 0}
    pending = VehicleOrientationSample.objects.filter(
        excluded=True, removal_pending=True
    ).order_by("id")[:limit]
    for sample in pending:
        try:
            camera_ai.delete_orientation_sample(sample.sample_id)
        except camera_ai.AiUnavailable as exc:
            sample.last_error = str(exc)[:200]
            sample.save(update_fields=["last_error", "updated_at"])
            counters["remove_failed"] += 1
            log.warning("Orientation sample removal stopped: Camera-PC unavailable: %s", exc)
            break
        except camera_ai.AiError as exc:
            sample.last_error = str(exc)[:200]
            sample.save(update_fields=["last_error", "updated_at"])
            counters["remove_failed"] += 1
            continue
        sample.removal_pending = False
        sample.sent_at = None
        sample.last_error = ""
        sample.save(update_fields=["removal_pending", "sent_at", "last_error", "updated_at"])
        counters["removed"] += 1
    return counters


def export_pending(*, limit: int) -> dict[str, int]:
    """Push labelled frames Camera-PC has not received yet; stops when it is down."""

    counters = {"sent": 0, "failed": 0, "missing": 0, "unavailable": 0}
    pending = (
        VehicleOrientationSample.objects.filter(
            sent_at__isnull=True, conflict=False, excluded=False
        )
        .exclude(last_error=PHOTO_MISSING)
        .order_by("id")[:limit]
    )
    for sample in pending:
        jpeg = _photo_bytes(sample)
        if jpeg is None:
            sample.last_error = PHOTO_MISSING
            sample.save(update_fields=["last_error", "updated_at"])
            counters["missing"] += 1
            continue
        try:
            camera_ai.post_orientation_sample(
                sample_id=sample.sample_id,
                label=sample.label,
                jpeg=jpeg,
                weight_kg=sample.weight_kg,
                captured_at=sample.captured_at.isoformat(),
            )
        except camera_ai.AiUnavailable as exc:
            sample.last_error = str(exc)[:200]
            sample.save(update_fields=["last_error", "updated_at"])
            counters["unavailable"] += 1
            log.warning("Orientation sample export stopped: Camera-PC unavailable: %s", exc)
            break
        except camera_ai.AiError as exc:
            sample.last_error = str(exc)[:200]
            sample.save(update_fields=["last_error", "updated_at"])
            counters["failed"] += 1
            continue
        sample.sent_at = timezone.now()
        sample.last_error = ""
        sample.save(update_fields=["sent_at", "last_error", "updated_at"])
        counters["sent"] += 1
    return counters


def run(*, limit: int | None = None) -> dict:
    """Collect labels, then export; the nightly task and the command share this."""

    if not settings.VEHICLE_ORIENTATION_DATASET_ENABLED:
        return {"enabled": False}
    collected = collect(limit=limit)
    batch = limit or settings.VEHICLE_ORIENTATION_EXPORT_BATCH
    removed = export_removals(limit=batch)
    exported = export_pending(limit=batch)
    conflicts = VehicleOrientationSample.objects.filter(conflict=True).count()
    summary = {"enabled": True, **collected, **removed, **exported, "conflicts": conflicts}
    log.info("Orientation dataset export %s", summary)
    return summary


__all__ = [
    "Label",
    "collect",
    "exclude_sample",
    "export_pending",
    "export_removals",
    "label_for_weight",
    "label_unassigned",
    "label_weighing",
    "load_records",
    "run",
    "set_manual_label",
]
