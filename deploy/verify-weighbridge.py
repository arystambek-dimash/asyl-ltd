#!/usr/bin/env python3
"""Run inside the backend container; SQL is read-only and images stay private."""

import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.db import connection, transaction
from apps.grain.weighing_audit import snapshot, probe

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '10s'")
    report, samples = snapshot(hours=24, sample_limit=3)

report["vision_samples"] = probe(samples) if report["config"]["vision_enabled"] else []
report["ok"] = bool(
    report["config"]["automatic_scale_enabled"]
    and report["config"]["vision_enabled"]
    and not report["uncovered_saved_weight_count"]
    and not report["processing_over_10_minutes"]
    and all("error_type" not in row for row in report["vision_samples"])
)
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report["ok"] else 1)
