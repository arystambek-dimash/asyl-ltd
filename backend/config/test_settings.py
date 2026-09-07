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
