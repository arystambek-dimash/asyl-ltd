import re

from django.db import migrations

# Копия правил номера на момент миграции: код приложения может измениться.
_SEPARATORS = re.compile(r"[\s\-.·]+")
_KZ_PLATE = re.compile(r"(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{3}[A-Z]{3})")
_CYRILLIC_TWINS = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def _plate(value):
    if not isinstance(value, str):
        return ""
    compact = _SEPARATORS.sub("", value.strip().upper().translate(_CYRILLIC_TWINS))
    if compact.startswith("KZ") and _KZ_PLATE.fullmatch(compact[2:]):
        compact = compact[2:]
    return compact if _KZ_PLATE.fullmatch(compact) else ""


def _reason(check):
    """Причина старого парного вердикта в кодах одиночного кадра; None — оставить."""
    if check.reason == "entry_evidence_pending":
        return "entry_missing"
    verdict = check.evidence.get("verdict")
    exit = verdict.get("exit") if isinstance(verdict, dict) else None
    exit = exit if isinstance(exit, dict) else {}
    plate = _plate(exit.get("plate"))
    if not plate or exit.get("plate_clear") is not True:
        return "plate_unreadable"
    snapshots = [row for row in check.evidence.get("entries") or [] if isinstance(row, dict)]
    if snapshots and all(row.get("number") not in ("", plate) for row in snapshots):
        return "entry_missing"
    return None


def store_legacy_review_reasons(apps, schema_editor):
    """Парные проверки до одиночного кадра: причину для экрана пишем в reason.

    Раньше её каждый раз выводил review_reason() из старого вердикта, а сами
    проверки без метки automatic_flow_version гонялись заново на каждом тике.
    """
    checks = apps.get_model("grain", "WeighingIdentityCheck").objects.using(
        schema_editor.connection.alias
    )
    legacy = checks.filter(status="review").exclude(
        evidence__has_key="automatic_flow_version"
    )
    for check in legacy.iterator(chunk_size=500):
        if not isinstance(check.evidence, dict):
            continue
        if check.reason != "entry_evidence_pending" and "verdict" not in check.evidence:
            continue
        reason = _reason(check)
        if reason and reason != check.reason:
            checks.filter(pk=check.pk).update(reason=reason)


class Migration(migrations.Migration):
    dependencies = [
        ("grain", "0022_remove_dead_grain_fields"),
    ]

    operations = [
        migrations.RunPython(store_legacy_review_reasons, migrations.RunPython.noop),
    ]
