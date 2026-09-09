"""Hardware client configuration, without web production/database prerequisites."""
from ._settings.base import *  # noqa: F403

SECRET_KEY = "unused-standalone-collector-no-http-server"
INSTALLED_APPS = []
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DATABASES = {}
