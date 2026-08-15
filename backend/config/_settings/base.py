import os
import re
import sys
from pathlib import Path
from typing import cast

BASE_DIR = Path(__file__).resolve().parents[2]
TESTING = "pytest" in sys.modules or os.environ.get("PYTEST_RUNNING") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "apps.common",
    "apps.sys_permissions.apps.SysPermissionsConfig",
    "apps.employees",
    "apps.accounts",
    "apps.catalog",
    "apps.clients",
    "apps.sales",
    "apps.eventlog",
    "apps.orders",
    "apps.warehouse",
    "apps.shipments",
    "apps.portal",
    "apps.notifications",
    "apps.cameras",
    "apps.conveyors",
    "apps.tasks",
    "apps.grain",
]

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",

    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("THROTTLE_ANON", "60/min"),
        "user": os.environ.get("THROTTLE_USER", "600/min"),
        "login": os.environ.get("THROTTLE_LOGIN", "10/min"),
        "register": os.environ.get("THROTTLE_REGISTER", "5/min"),
        "portal_order_create": os.environ.get(
            "THROTTLE_PORTAL_ORDER_CREATE", "10/min"
        ),
        "truck_scale_preview": os.environ.get(
            "THROTTLE_TRUCK_SCALE_PREVIEW", "120/min"
        ),
        "conveyor_device": os.environ.get(
            "THROTTLE_CONVEYOR_DEVICE", "180/min"
        ),
        "conveyor_ai": os.environ.get(
            "THROTTLE_CONVEYOR_AI", "6000/min"
        ),
    },
    "NUM_PROXIES": int(os.environ.get("THROTTLE_NUM_PROXIES", "0")),
}

if TESTING:
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()
    throttle_rates = cast(
        dict[str, object], REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    )
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        key: None for key in throttle_rates
    }

SIMPLE_JWT = {
    "CHECK_REVOKE_TOKEN": True,
}

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "asyl"),
        "USER": os.environ.get("DB_USER", "asyl"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "asyl"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "ru"

TIME_ZONE = "Asia/Almaty"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024))
)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

_redis_url = os.environ.get("REDIS_URL", "").strip()
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }

APIPAY_API_KEY = os.environ.get("APIPAY_API_KEY", "").strip()
APIPAY_WEBHOOK_SECRET = os.environ.get("APIPAY_WEBHOOK_SECRET", "").strip()
APIPAY_BASE_URL = os.environ.get(
    "APIPAY_BASE_URL", "https://api.apipay.kz/api/v1"
).rstrip("/")
APIPAY_TIMEOUT_SECONDS = float(os.environ.get("APIPAY_TIMEOUT_SECONDS", "10"))

TRUCK_SCALE_API_URL = os.environ.get("TRUCK_SCALE_API_URL", "").strip()
try:
    TRUCK_SCALE_TIMEOUT_SECONDS = float(
        os.environ.get("TRUCK_SCALE_TIMEOUT_SECONDS", "3")
    )
except (TypeError, ValueError):
    TRUCK_SCALE_TIMEOUT_SECONDS = 3.0
if not 0 < TRUCK_SCALE_TIMEOUT_SECONDS < float("inf"):
    TRUCK_SCALE_TIMEOUT_SECONDS = 3.0

try:
    # The operator display should fail quickly; capture commands retain the
    # longer timeout above because they are explicit user actions.
    TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS = float(
        os.environ.get("TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS", "1")
    )
except (TypeError, ValueError):
    TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS = 1.0
if not 0 < TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS < float("inf"):
    TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS = 1.0

try:
    TRUCK_SCALE_MAX_AGE_SECONDS = float(
        os.environ.get("TRUCK_SCALE_MAX_AGE_SECONDS", "5")
    )
except (TypeError, ValueError):
    TRUCK_SCALE_MAX_AGE_SECONDS = 5.0
if not 0 <= TRUCK_SCALE_MAX_AGE_SECONDS < float("inf"):
    TRUCK_SCALE_MAX_AGE_SECONDS = 5.0

try:
    TRUCK_SCALE_MAX_WEIGHT_KG = int(
        os.environ.get("TRUCK_SCALE_MAX_WEIGHT_KG", "100000")
    )
except (TypeError, ValueError):
    TRUCK_SCALE_MAX_WEIGHT_KG = 100_000
if TRUCK_SCALE_MAX_WEIGHT_KG <= 0:
    TRUCK_SCALE_MAX_WEIGHT_KG = 100_000

INVOICE_SUPPLIER = {
    "short_name": os.environ.get("INVOICE_SUPPLIER_SHORT_NAME", "АСЫЛ-LTD"),
    "legal_name": os.environ.get(
        "INVOICE_SUPPLIER_LEGAL_NAME",
        'ТОВАРИЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "АСЫЛ-LTD"',
    ),
    "bin": os.environ.get("INVOICE_SUPPLIER_BIN", "020740000305"),
    "iban": os.environ.get("INVOICE_SUPPLIER_IBAN", "KZ6696516F0007929746"),
    "kbe": os.environ.get("INVOICE_SUPPLIER_KBE", "17"),
    "bank": os.environ.get("INVOICE_SUPPLIER_BANK", 'АО "ForteBank"'),
    "bic": os.environ.get("INVOICE_SUPPLIER_BIC", "IRTYKZKA"),
    "payment_code": os.environ.get("INVOICE_PAYMENT_CODE", "710"),
    "address": os.environ.get(
        "INVOICE_SUPPLIER_ADDRESS",
        "Шымкент, Аль-Фарабийский район, улица Руставелли, д. 18",
    ),
    "vat_rate": os.environ.get("INVOICE_VAT_RATE", "16"),
}

CAMERA_HOST = os.environ.get("CAMERA_HOST") or "100.109.156.107"
CAMERA_PORT = int(os.environ.get("CAMERA_PORT") or "8554")
CAMERA_USER = os.environ.get("CAMERA_USER") or "viewer"
CAMERA_PASS = os.environ.get("CAMERA_PASS", "")
GO2RTC_API_URL = (os.environ.get("GO2RTC_API_URL") or "").rstrip("/")
CAMERA_PLAYBACK_URL = (
    os.environ.get("CAMERA_PLAYBACK_URL") or f"http://{CAMERA_HOST}:9996"
).rstrip("/")
CAMERA_ALERT_WEBHOOK_URL = os.environ.get("CAMERA_ALERT_WEBHOOK_URL", "").strip()
CAMERA_ALERT_WEBHOOK_TOKEN = os.environ.get("CAMERA_ALERT_WEBHOOK_TOKEN", "").strip()
CAMERA_ALERT_TELEGRAM_BOT_TOKEN = os.environ.get(
    "CAMERA_ALERT_TELEGRAM_BOT_TOKEN", ""
).strip()
CAMERA_ALERT_TELEGRAM_CHAT_ID = os.environ.get(
    "CAMERA_ALERT_TELEGRAM_CHAT_ID", ""
).strip()

# Django is the only holder of the plaintext service key. The camera PC stores
# only its SHA-256 digest and validates the X-Api-Key header sent by the client.
AI_SERVICE_URL = (
    os.environ.get("AI_SERVICE_URL") or f"http://{CAMERA_HOST}:8890"
).rstrip("/")
AI_SERVICE_API_KEY = os.environ.get("AI_SERVICE_API_KEY", "").strip()
try:
    AI_SERVICE_TIMEOUT = float(os.environ.get("AI_SERVICE_TIMEOUT", "25"))
except (TypeError, ValueError):
    AI_SERVICE_TIMEOUT = 25.0
if not 0 < AI_SERVICE_TIMEOUT < float("inf"):
    AI_SERVICE_TIMEOUT = 25.0


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


CONVEYOR_AI_CALLBACK_TOKEN_SHA256 = os.environ.get(
    "CONVEYOR_AI_CALLBACK_TOKEN_SHA256", ""
).strip().lower()
if (
    CONVEYOR_AI_CALLBACK_TOKEN_SHA256
    and re.fullmatch(r"[0-9a-f]{64}", CONVEYOR_AI_CALLBACK_TOKEN_SHA256) is None
):
    raise ValueError(
        "CONVEYOR_AI_CALLBACK_TOKEN_SHA256 must be a lowercase SHA-256 digest"
    )
CONVEYOR_DEVICE_SYNC_MS = _bounded_int_env(
    "CONVEYOR_DEVICE_SYNC_MS", 500, 100, 500
)
CONVEYOR_DEVICE_LEASE_MS = _bounded_int_env(
    "CONVEYOR_DEVICE_LEASE_MS", 1200, CONVEYOR_DEVICE_SYNC_MS + 100, 1500
)
CONVEYOR_DEVICE_FRESH_MS = _bounded_int_env(
    "CONVEYOR_DEVICE_FRESH_MS", 1500, CONVEYOR_DEVICE_LEASE_MS, 5000
)
CONVEYOR_AI_STALE_MS = _bounded_int_env(
    "CONVEYOR_AI_STALE_MS", 1500, 500, 5000
)
CONVEYOR_COMMAND_TIMEOUT_SECONDS = _bounded_int_env(
    "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5, 1, 10
)
CONVEYOR_NO_PROGRESS_SECONDS = _bounded_int_env(
    "CONVEYOR_NO_PROGRESS_SECONDS", 15, 1, 30
)
CONVEYOR_MAX_RUN_SECONDS = _bounded_int_env(
    "CONVEYOR_MAX_RUN_SECONDS", 300, 1, 900
)
