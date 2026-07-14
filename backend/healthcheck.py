"""Docker healthcheck бэкенда. Любой HTTP-ответ (в т.ч. 401 без токена)
означает, что gunicorn/Django живы и принимают запросы. Отказ соединения
или таймаут — контейнер нездоров."""
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/api/auth/me/"

try:
    urllib.request.urlopen(URL, timeout=4)
except urllib.error.HTTPError:
    sys.exit(0)  # 401/403 и т.п. — процесс отвечает
except Exception:
    sys.exit(1)  # соединение не установлено — нездоров
sys.exit(0)
