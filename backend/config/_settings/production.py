import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import REST_FRAMEWORK

DEBUG = False
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

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
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

CORS_ALLOWED_ORIGINS = [
    value.strip()
    for value in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if value.strip()
]

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    value.strip()
    for value in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if value.strip()
]

ALLOWED_HOSTS = [
    value.strip()
    for value in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if value.strip()
]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must contain explicit production hosts"
    )

REST_FRAMEWORK["NUM_PROXIES"] = int(
    os.environ.get("THROTTLE_NUM_PROXIES", "1")
)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
