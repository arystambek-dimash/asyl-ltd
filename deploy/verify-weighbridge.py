#!/usr/bin/env python3
"""Run inside the backend container; SQL is read-only and images stay private."""

import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.db import connection, transaction
from django.conf import settings
from apps.grain.weighing_audit import snapshot, probe
from apps.grain.weighing_audit import public_summary
from apps.grain import scale

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '10s'")
    report, samples = snapshot(hours=24, sample_limit=3)

report["vision_samples"] = probe(samples) if report["config"]["vision_enabled"] else []
try:
    observation = scale.read_truck_scale_observation(scale.TRUCK_SCALE_KEY)
    report["scale_probe"] = {
        "state": observation.state,
        "connected": observation.connected,
        "stable": observation.stable,
        "weight_kg": str(observation.weight_kg),
        "age_seconds": str(observation.age_seconds),
        "updated_at": observation.updated_at,
        "max_age_seconds": settings.TRUCK_SCALE_MAX_AGE_SECONDS,
    }
except Exception as exc:
    report["scale_probe"] = {"error_type": type(exc).__name__}
report["code_scope"] = (
    "reviewed diagnostic and candidate vision wrapper; no server files changed"
)
report["ok"] = bool(
    report["config"]["automatic_scale_enabled"]
    and report["config"]["vision_enabled"]
    and not report["uncovered_saved_weight_count"]
    and not report["processing_over_10_minutes"]
    and report["scale_probe"].get("state") == "ready"
    and bool(report["vision_samples"])
    and all("error_type" not in row for row in report["vision_samples"])
    and all(
        row.get("pair_reading_matches") is not False for row in report["vision_samples"]
    )
)
print(json.dumps(public_summary(report), ensure_ascii=False, indent=2))
sys.exit(0 if report["ok"] else 1)
