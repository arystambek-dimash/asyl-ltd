from django.db import migrations, models

LEGACY_BRAND = "unclassified"


def _legacy_breakdown(total):
    total = max(0, int(total or 0))
    return {LEGACY_BRAND: total} if total else {}


def backfill_legacy_brands(apps, schema_editor):
    daily_analytics = apps.get_model("cameras", "AlwaysOnDailyAnalytics")
    count_archive = apps.get_model("cameras", "AlwaysOnCountArchive")

    for row in daily_analytics.objects.only("id", "model_total").iterator(
        chunk_size=1000
    ):
        row.model_per_brand = _legacy_breakdown(row.model_total)
        row.save(update_fields=["model_per_brand"])

    for archive in count_archive.objects.only(
        "id",
        "model_total",
        "day_rows",
    ).iterator(chunk_size=1000):
        archive.model_per_brand = _legacy_breakdown(archive.model_total)
        day_rows = []
        for raw_snapshot in archive.day_rows or []:
            if not isinstance(raw_snapshot, dict):
                day_rows.append(raw_snapshot)
                continue
            snapshot = dict(raw_snapshot)
            snapshot["model_per_brand"] = _legacy_breakdown(
                snapshot.get("model_total")
            )
            day_rows.append(snapshot)
        archive.day_rows = day_rows
        archive.save(update_fields=["model_per_brand", "day_rows"])


def remove_snapshot_brand_breakdowns(apps, schema_editor):
    count_archive = apps.get_model("cameras", "AlwaysOnCountArchive")
    for archive in count_archive.objects.only("id", "day_rows").iterator(
        chunk_size=1000
    ):
        changed = False
        day_rows = []
        for raw_snapshot in archive.day_rows or []:
            if not isinstance(raw_snapshot, dict):
                day_rows.append(raw_snapshot)
                continue
            snapshot = dict(raw_snapshot)
            if "model_per_brand" in snapshot:
                snapshot.pop("model_per_brand")
                changed = True
            day_rows.append(snapshot)
        if changed:
            archive.day_rows = day_rows
            archive.save(update_fields=["day_rows"])


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0024_always_on_imported_event_classification"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysondailyanalytics",
            name="model_per_brand",
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="alwaysoncountarchive",
            name="model_per_brand",
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
        migrations.RunPython(
            backfill_legacy_brands,
            remove_snapshot_brand_breakdowns,
        ),
        migrations.AddConstraint(
            model_name="alwaysonimportedevent",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(color_confidence__isnull=True)
                    | (
                        models.Q(color_confidence__gte=0)
                        & models.Q(color_confidence__lte=1)
                    )
                ),
                name="cameras_event_color_conf_01",
            ),
        ),
        migrations.AddConstraint(
            model_name="alwaysonimportedevent",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(brand_confidence__isnull=True)
                    | (
                        models.Q(brand_confidence__gte=0)
                        & models.Q(brand_confidence__lte=1)
                    )
                ),
                name="cameras_event_brand_conf_01",
            ),
        ),
    ]
