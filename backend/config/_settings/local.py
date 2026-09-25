import os

from .base import *  # noqa: F403
from .base import env_list

DEBUG = True
SECRET_KEY = (
        os.environ.get("SECRET_KEY", "").strip()
        or "django-insecure-local-development-only"
)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver")
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
