from rest_framework.exceptions import ValidationError

from apps.sales.models import Department

STATEMENT_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def statement_departments(params):
    raw = params.get("departments")
    if raw is None:
        raw = params.get("department")
    if raw is None:
        return None
    codes = tuple(dict.fromkeys(
        code.strip() for code in raw.split(",") if code.strip()
    ))
    if not codes:
        raise ValidationError({
            "detail": "Выберите хотя бы один отдел",
            "code": "departments_required",
        })
    known = set(
        Department.objects.filter(code__in=codes).values_list(
            "code",
            flat=True,
        )
    )
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise ValidationError({
            "detail": "Неизвестный отдел: " + ", ".join(unknown),
            "code": "bad_department",
        })
    return codes


def statement_format(params) -> str:
    value = (params.get("export") or "xlsx").strip().lower()
    if value not in STATEMENT_CONTENT_TYPES:
        raise ValidationError({
            "detail": "Формат выписки: xlsx или pdf",
            "code": "bad_statement_format",
        })
    return value


def statement_sections(params, available):
    raw = params.get("sections")
    if raw is None:
        return None
    keys = tuple(dict.fromkeys(
        key.strip() for key in raw.split(",") if key.strip()
    ))
    if not keys:
        raise ValidationError({
            "detail": "Выберите хотя бы один раздел выписки",
            "code": "sections_required",
        })
    unknown = [key for key in keys if key not in available]
    if unknown:
        raise ValidationError({
            "detail": "Неизвестный раздел: " + ", ".join(unknown),
            "code": "bad_section",
        })
    return keys
