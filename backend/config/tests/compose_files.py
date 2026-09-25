"""Общие хелперы тестов compose: файлы читаются как текст, сервис — по отступу."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def read_compose(compose_file: str) -> str:
    return (REPO_ROOT / compose_file).read_text(encoding="utf-8")


def service_block(compose: str, service: str) -> str:
    lines = compose[compose.index(f"  {service}:\n") :].splitlines(keepends=True)
    end = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("  ") and not line.startswith("    "):
            end = index
            break
    return "".join(lines[:end])
