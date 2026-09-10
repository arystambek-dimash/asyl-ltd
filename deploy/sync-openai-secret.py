#!/usr/bin/env python3
"""Persist explicitly supplied OpenAI configuration without printing values."""

import base64
import os
from pathlib import Path
import re
import tempfile


def sync(path):
    updates = {}
    encoded = os.environ.get("OPENAI_API_KEY_B64")
    if encoded:
        key = base64.b64decode(encoded, validate=True).decode("ascii")
        if not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,500}", key):
            raise ValueError("Invalid OpenAI key format")
        updates["OPENAI_API_KEY"] = key
    encoded_model = os.environ.get("SHIPPING_WAGON_AI_MODEL_B64")
    if encoded_model:
        model = base64.b64decode(encoded_model, validate=True).decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", model):
            raise ValueError("Invalid shipping wagon model")
        updates["SHIPPING_WAGON_AI_MODEL"] = model
    encoded_detail = os.environ.get("SHIPPING_WAGON_AI_DETAIL_B64")
    if encoded_detail:
        detail = base64.b64decode(encoded_detail, validate=True).decode("ascii").strip()
        if detail not in {"high", "original"}:
            raise ValueError("Invalid shipping wagon image detail")
        updates["SHIPPING_WAGON_AI_DETAIL"] = detail
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
