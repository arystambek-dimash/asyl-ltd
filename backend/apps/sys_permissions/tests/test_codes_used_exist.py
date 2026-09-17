"""Каждый код права, который проверяет бэкенд или фронт, существует в каталоге.

Иначе право нельзя выдать никому, кроме суперпользователя (так было с grain.edit).
"""
import re
from pathlib import Path

from apps.sys_permissions.perms import ALL_CODES, SECTION_ORDER

ROOT = Path(__file__).resolve().parents[4]
# Разделы и действия каталога плюс снятые: так сканер ловит права, а не имена задач
# celery («orders.reconcile_apipay») и полей сериализаторов («warehouse.name»).
RETIRED_SECTIONS = ["shipping", "train", "ai_247"]
RETIRED_ACTIONS = ["load", "ship", "debt_override", "lab", "dispatch", "unload", "exit"]
ACTIONS = {code.split(".", 1)[1] for code in ALL_CODES} | set(RETIRED_ACTIONS)


def _alternation(words):
    return "|".join(sorted(words, key=len, reverse=True))


CODE = re.compile(
    rf"""["']((?:{_alternation([*SECTION_ORDER, *RETIRED_SECTIONS])})\.(?:{_alternation(ACTIONS)}))["']"""
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
