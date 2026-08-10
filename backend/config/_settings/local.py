import os

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = (
        os.environ.get("SECRET_KEY", "").strip()
        or "django-insecure-local-development-only"
)

ALLOWED_HOSTS = [
    value.strip()
    for value in os.environ.get(
        "ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver"
    ).split(",")
    if value.strip()
]
CORS_ALLOWED_ORIGINS = [
    value.strip()
    for value in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if value.strip()
]
CSRF_TRUSTED_ORIGINS = [
    value.strip()
    for value in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if value.strip()
]

CORS_ALLOW_CREDENTIALS = True
