"""Импорт config._settings.base в отдельном процессе: кривой env не портит процесс pytest."""
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def import_base_settings(env: dict[str, str], *names: str) -> subprocess.CompletedProcess[str]:
    """Печатает JSON-список значений ``names`` из base; ошибка настроек — в stderr."""
    code = (
        "import json; from config._settings import base; "
        f"print(json.dumps([getattr(base, name) for name in {list(names)!r}]))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
