import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    APP_ENVIRONMENT,
    APP_RELEASE,
    APP_SERVICE,
    LOG_LEVEL,
    REST_FRAMEWORK,
    build_logging_config,
    env_list,
)

DEBUG = False
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

# Production emits one JSON object per application log line.  Local settings
# keep the readable formatter unless LOG_FORMAT=json is explicitly requested.
LOG_FORMAT = os.environ.get("LOG_FORMAT", "json").strip() or "json"
LOGGING = build_logging_config(
    log_format=LOG_FORMAT,
    level=LOG_LEVEL,
    service=APP_SERVICE,
    environment=APP_ENVIRONMENT,
    release=APP_RELEASE,
)

if (
        len(SECRET_KEY) < 50
        or len(set(SECRET_KEY)) < 5
        or SECRET_KEY.startswith("django-insecure-")
):
    raise ImproperlyConfigured(
        "SECRET_KEY must be a strong, non-development value in production"
    )

if not os.environ.get("DB_PASSWORD", ""):
    raise ImproperlyConfigured("DB_PASSWORD must be set in production")

if not os.environ.get("REDIS_URL", "").strip():
    raise ImproperlyConfigured("REDIS_URL must be set in production")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Снаружи эти заголовки (и X-Frame-Options) отдаёт только nginx
# (deploy/nginx/conf.d/snippets/security-headers.conf), копии Django он
# скрывает. Здесь они остаются на случай запроса к бэкенду в обход nginx.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Локальный Next.js dev-сервер (npm run dev) должен уметь ходить в prod-API,
# когда во фронте NEXT_PUBLIC_API_URL указывает на прод. Аутентификация —
# JWT Bearer (не куки), поэтому разрешение http-localhost не открывает доступ
# к сессиям и безопасно как базовый дефолт. Доп. origin задаются через env.
LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _merge_origins(*groups):
    merged = []
    for group in groups:
        for origin in group:
            if origin not in merged:
                merged.append(origin)
    return merged


CORS_ALLOWED_ORIGINS = _merge_origins(
    LOCAL_DEV_ORIGINS,
    env_list("CORS_ALLOWED_ORIGINS"),
)

CSRF_TRUSTED_ORIGINS = _merge_origins(
    LOCAL_DEV_ORIGINS,
    env_list("CSRF_TRUSTED_ORIGINS"),
)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must contain explicit production hosts"
    )

REST_FRAMEWORK["NUM_PROXIES"] = 1
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
