import os
import re
import sys
from pathlib import Path
from typing import cast

from config.observability import (
    build_logging_config,
    env_flag,
    initialize_sentry,
    sample_rate_from_env,
)

BASE_DIR = Path(__file__).resolve().parents[2]
TESTING = "pytest" in sys.modules or os.environ.get("PYTEST_RUNNING") == "1"

APP_RELEASE = os.environ.get("APP_RELEASE", "development").strip() or "development"
_DEFAULT_APP_ENVIRONMENT = (
    "development" if os.environ.get("DEBUG", "1") == "1" else "production"
)
APP_ENVIRONMENT = (
    os.environ.get("APP_ENVIRONMENT", _DEFAULT_APP_ENVIRONMENT).strip()
    or _DEFAULT_APP_ENVIRONMENT
)
APP_SERVICE = os.environ.get("APP_SERVICE", "backend").strip() or "backend"
LOG_FORMAT = os.environ.get("LOG_FORMAT", "readable").strip() or "readable"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"

SENTRY_BACKEND_DSN = os.environ.get("SENTRY_BACKEND_DSN", "").strip()
SENTRY_ENABLE_LOGS = env_flag(os.environ.get("SENTRY_ENABLE_LOGS", "0"))
SENTRY_TRACES_SAMPLE_RATE = sample_rate_from_env(
    os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0"),
    name="SENTRY_TRACES_SAMPLE_RATE",
)
SENTRY_PROFILES_SAMPLE_RATE = sample_rate_from_env(
    os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0"),
    name="SENTRY_PROFILES_SAMPLE_RATE",
)

LOGGING = build_logging_config(
    log_format=LOG_FORMAT,
    level=LOG_LEVEL,
    service=APP_SERVICE,
    environment=APP_ENVIRONMENT,
    release=APP_RELEASE,
)

initialize_sentry(
    dsn=SENTRY_BACKEND_DSN,
    release=APP_RELEASE,
    environment=APP_ENVIRONMENT,
    service=APP_SERVICE,
    enable_logs=SENTRY_ENABLE_LOGS,
    traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
)

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

# Celery is introduced narrowly for ApiPay reconciliation. Results are never
# stored: the task's durable effects and the heartbeat are the source of truth.
CELERY_BROKER_URL = (
    os.environ.get("CELERY_BROKER_URL", "").strip()
    or _redis_url
    or "redis://localhost:6379/0"
)
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED = False
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_EVENT_SERIALIZER = "json"
CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "orders.reconcile_apipay": {"queue": "payments"},
}

try:
    _apipay_reconcile_interval = max(
        15,
        int(os.environ.get("APIPAY_RECONCILE_INTERVAL_SECONDS", "30")),
    )
except ValueError:
    _apipay_reconcile_interval = 30

try:
    _apipay_reconcile_max_backoff = max(
        _apipay_reconcile_interval,
        int(os.environ.get("APIPAY_MONITOR_MAX_BACKOFF_SECONDS", "300")),
    )
except ValueError:
    _apipay_reconcile_max_backoff = max(_apipay_reconcile_interval, 300)
try:
    _apipay_task_lock_seconds = max(
        _apipay_reconcile_max_backoff + _apipay_reconcile_interval + 60,
        int(os.environ.get("APIPAY_RECONCILE_TASK_LOCK_SECONDS", "1200")),
    )
except ValueError:
    _apipay_task_lock_seconds = max(
        _apipay_reconcile_max_backoff + _apipay_reconcile_interval + 60,
        1200,
    )

# A hard-killed late-acked task becomes visible no later than its singleton
# lease expires. Fresh beat messages keep a replacement worker observable in
# the meantime, and exactly one owner can claim work at this boundary.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": _apipay_task_lock_seconds,
}

CELERY_BEAT_SCHEDULE = {
    "reconcile-apipay": {
        "task": "orders.reconcile_apipay",
        "schedule": _apipay_reconcile_interval,
        "options": {
            "queue": "payments",
            # A delayed periodic message is obsolete once the next interval is
            # due. Explicit task retries override this with their own expiry.
            "expires": max(1, _apipay_reconcile_interval - 1),
        },
    },
}
# Persist last_run_at after every dispatch so the bounded beat schedule file
# is both crash-safe and useful to operators during health diagnosis.
CELERY_BEAT_SYNC_EVERY = 1

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

_legacy_bridge_cameras = os.environ.get(
    "CONVEYOR_LEGACY_BRIDGE_CAMERAS", ""
)
CONVEYOR_LEGACY_BRIDGE_CAMERAS = frozenset(
    camera.strip() for camera in _legacy_bridge_cameras.split(",")
    if camera.strip()
)
if any(
    re.fullmatch(r"cam[1-9][0-9]*", camera) is None
    for camera in CONVEYOR_LEGACY_BRIDGE_CAMERAS
):
    raise ValueError(
        "CONVEYOR_LEGACY_BRIDGE_CAMERAS must be a comma-separated list of camN"
    )
CONVEYOR_LEGACY_BRIDGE_POLL_MS = _bounded_int_env(
    "CONVEYOR_LEGACY_BRIDGE_POLL_MS", 250, 100, 500
)
CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS = _bounded_int_env(
    "CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS", 350, 50, 400
)
CONVEYOR_LEGACY_BRIDGE_STALE_MS = _bounded_int_env(
    "CONVEYOR_LEGACY_BRIDGE_STALE_MS", 750, 500, 1000
)
if (
    CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS
    + CONVEYOR_LEGACY_BRIDGE_POLL_MS
    > CONVEYOR_LEGACY_BRIDGE_STALE_MS
):
    raise ValueError(
        "legacy bridge request timeout plus poll interval must be at most "
        "CONVEYOR_LEGACY_BRIDGE_STALE_MS"
    )
CONVEYOR_LEGACY_BRIDGE_DEVICE_SYNC_MS = _bounded_int_env(
    "CONVEYOR_LEGACY_BRIDGE_DEVICE_SYNC_MS", 250, 100, 500
)
CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS = _bounded_int_env(
    "CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS", 750, 200, 1000
)
if (
    CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS
    < CONVEYOR_LEGACY_BRIDGE_DEVICE_SYNC_MS + 100
):
    raise ValueError(
        "CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS must exceed sync by 100 ms"
    )
if (
    CONVEYOR_LEGACY_BRIDGE_STALE_MS
    + CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS
    > 1500
):
    raise ValueError(
        "legacy bridge stale window plus ESP lease must be at most 1500 ms"
    )
