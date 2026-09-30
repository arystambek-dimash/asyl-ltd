"""Импорт модуля config._settings в отдельном процессе: кривой env не портит процесс pytest."""
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def import_settings(
    env: dict[str, str], *names: str, module: str = "base"
) -> subprocess.CompletedProcess[str]:
    """Печатает JSON-список значений ``names`` из ``module``; ошибка настроек — в stderr."""
    code = (
        f"import json; from config._settings import {module} as settings; "
        f"print(json.dumps([getattr(settings, name) for name in {list(names)!r}]))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
