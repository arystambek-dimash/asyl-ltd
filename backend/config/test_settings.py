"""Test runtime: exercise password/auth flows without production PBKDF cost."""

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403
from ._settings.base import TESTING

if not TESTING:
    raise ImproperlyConfigured("config.test_settings may only be used by tests")

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Ordinary TestCase transactions are private to the main connection. Dedicated
# transaction=True integration tests opt into the production worker pool.
CAMERA_EVENT_SYNC_WORKERS = 1

# ПК цеха в тестах всегда «недоступен» и отказывает сразу: порт 9 на loopback
# никто не слушает. Иначе незамоканный best-effort запрос (снимок весовой,
# настройки ориентации) ждёт таймаут соединения к боевому CAMERA_HOST.
# Окружение разработчика эти адреса не переопределяет.
CAMERA_HOST = "127.0.0.1"
CAMERA_PORT = 9
AI_SERVICE_URL = f"http://{CAMERA_HOST}:{CAMERA_PORT}"
GO2RTC_API_URL = ""
