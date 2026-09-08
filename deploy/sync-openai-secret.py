#!/usr/bin/env python3
"""Persist an explicitly supplied Actions secret without printing its value."""

import base64
import os
from pathlib import Path
import re
import tempfile


def sync(path):
    encoded = os.environ.get("OPENAI_API_KEY_B64")
    if encoded is None or encoded == "":
        return  # Manual deploys and unset Actions secret preserve server config.
    key = base64.b64decode(encoded, validate=True).decode("ascii")
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,500}", key):
        raise ValueError("Invalid OpenAI key format")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Server .env must be an existing regular file")
    lines = [
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=", line)
    ]
    lines.append("OPENAI_API_KEY=" + key)
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
        raise SystemExit("Could not synchronize OpenAI secret") from None
