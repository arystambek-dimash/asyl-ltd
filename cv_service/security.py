from __future__ import annotations

import hashlib
import hmac


def valid_api_key(candidate: str | None, expected_sha256: str) -> bool:
    if not candidate:
        return False
    actual = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, expected_sha256)
