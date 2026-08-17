"""Docker healthcheck бэкенда. Ответ 4xx (в т.ч. 401 без токена) означает, что
gunicorn/Django живы и принимают запросы. Ответ 5xx (например, потеряна БД),
отказ соединения или таймаут — контейнер нездоров."""

import os
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/api/auth/me/"


def _healthcheck_host() -> str:
    """Use a configured Django host while connecting over loopback."""

    for value in os.environ.get("ALLOWED_HOSTS", "localhost").split(","):
        host = value.strip()
        if host and host != "*" and not host.startswith("."):
            return host
    return "localhost"


def main() -> int:
    request = urllib.request.Request(
        URL,
        headers={"Host": _healthcheck_host()},
    )
    try:
        urllib.request.urlopen(request, timeout=4)
    except urllib.error.HTTPError as exc:
        return 0 if exc.code < 500 else 1
    except OSError:
        return 1  # соединение не установлено — нездоров
    return 0


if __name__ == "__main__":
    sys.exit(main())
