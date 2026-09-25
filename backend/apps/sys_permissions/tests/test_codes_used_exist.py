"""Каждый код права, который проверяет бэкенд или фронт, существует в каталоге.

Иначе право нельзя выдать никому, кроме суперпользователя (так было с grain.edit).
"""
import re
from pathlib import Path

from apps.sys_permissions.perms import ALL_CODES, SECTION_LABELS
from apps.sys_permissions.tests.test_catalog import RETIRED_CODES

ROOT = Path(__file__).resolve().parents[4]
# Разделы и действия каталога плюс снятые: так сканер ловит права, а не имена задач
# celery («orders.reconcile_apipay») и полей сериализаторов («warehouse.name»).
SECTIONS = set(SECTION_LABELS) | {code.split(".", 1)[0] for code in RETIRED_CODES}
ACTIONS = {code.split(".", 1)[1] for code in ALL_CODES | RETIRED_CODES}


def _alternation(words):
    return "|".join(sorted(words, key=len, reverse=True))


CODE = re.compile(
    rf"""["']((?:{_alternation(SECTIONS)})\.(?:{_alternation(ACTIONS)}))["']"""
)


def _sources():
    backend = ROOT / "backend" / "apps"
    for path in backend.rglob("*.py"):
        if {"tests", "migrations"} & set(path.parts) or path.name in {"migration_data.py", "conftest.py"}:
            continue
        yield path
    frontend = ROOT / "frontend" / "src"
    if frontend.exists():
        for path in frontend.rglob("*.ts*"):
            if ".test." not in path.name:
                yield path


def test_every_permission_code_used_in_code_is_in_the_catalog():
    missing = {}
    for path in _sources():
        for code in CODE.findall(path.read_text(encoding="utf-8")):
            if code not in ALL_CODES:
                missing.setdefault(code, set()).add(str(path.relative_to(ROOT)))
    assert missing == {}


def test_role_presets_use_only_catalog_codes():
    """Шаблоны ролей пикера: сканер выше не ловит опечатку в разделе («payment.view»)."""
    presets = ROOT / "frontend" / "src" / "lib" / "permission-presets.ts"
    if not presets.exists():
        return
    codes = {
        code
        for block in re.findall(r"codes:\s*\[([^\]]*)\]", presets.read_text(encoding="utf-8"))
        for code in re.findall(r"""["']([^"']+)["']""", block)
    }
    assert codes, "в шаблонах ролей не нашлось ни одного кода"
    assert codes - ALL_CODES == set()
