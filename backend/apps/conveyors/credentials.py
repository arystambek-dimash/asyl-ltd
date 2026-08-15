import hashlib
import re
import secrets
import uuid

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def generate_token() -> str:
    """Return exactly 256 bits encoded without padding."""
    token = secrets.token_urlsafe(32)
    if not TOKEN_RE.fullmatch(token):  # pragma: no cover - stdlib invariant
        raise RuntimeError("unexpected token encoding")
    return token


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def authorization_value(public_id: uuid.UUID, token: str) -> str:
    return f"Device {public_id}.{token}"
