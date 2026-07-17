"""Docker healthcheck бэкенда. Ответ 4xx (в т.ч. 401 без токена) означает, что
gunicorn/Django живы и принимают запросы. Ответ 5xx (например, потеряна БД),
отказ соединения или таймаут — контейнер нездоров."""
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/api/auth/me/"

try:
    urllib.request.urlopen(URL, timeout=4)
except urllib.error.HTTPError as exc:
    sys.exit(0 if exc.code < 500 else 1)
except Exception:
    sys.exit(1)  # соединение не установлено — нездоров
sys.exit(0)
