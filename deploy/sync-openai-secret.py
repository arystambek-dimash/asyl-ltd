#!/usr/bin/env python3
"""Persist explicitly supplied OpenAI configuration without printing values."""

import base64
import os
from pathlib import Path
import re
import tempfile


# Actions variable -> (.env name, accepted value, strip surrounding whitespace, error).
SETTINGS = {
    "OPENAI_API_KEY_B64": (
        "OPENAI_API_KEY", r"sk-[A-Za-z0-9_-]{20,500}", False, "Invalid OpenAI key format"
    ),
    "SHIPPING_WAGON_AI_MODEL_B64": (
        "SHIPPING_WAGON_AI_MODEL",
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}",
        True,
        "Invalid shipping wagon model",
    ),
    "SHIPPING_WAGON_AI_DETAIL_B64": (
        "SHIPPING_WAGON_AI_DETAIL", r"high|original", True, "Invalid shipping wagon image detail"
    ),
}


def sync(path):
    updates = {}
    for variable, (name, pattern, strip, error) in SETTINGS.items():
        encoded = os.environ.get(variable)
        if not encoded:
            continue
        value = base64.b64decode(encoded, validate=True).decode("ascii")
        if strip:
            value = value.strip()
        if not re.fullmatch(pattern, value):
            raise ValueError(error)
        updates[name] = value
    if not updates:
        return  # Unset Actions values/manual deploys preserve server config.
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Server .env must be an existing regular file")
    lines = [
        line
        for line in path.read_text().splitlines()
        if not any(re.match(r"^\s*(?:export\s+)?" + name + r"\s*=", line) for name in updates)
    ]
    lines.extend(name + "=" + value for name, value in updates.items())
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=".openai-env-", delete=False
        ) as stream:
            name = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


if __name__ == "__main__":
    try:
        sync(".env")
    except (ValueError, OSError):
        raise SystemExit("Could not synchronize OpenAI configuration") from None
