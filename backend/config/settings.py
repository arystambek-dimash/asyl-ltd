import os
import sys
from pathlib import Path
from typing import cast

BASE_DIR = Path(__file__).resolve().parent.parent

# Под pytest троттлинг выключаем: общие фикстуры-юзеры делают много запросов
# и упирались бы в лимиты. Прицельные тесты троттла включают его локально.
TESTING = "pytest" in sys.modules or os.environ.get("PYTEST_RUNNING") == "1"

# Preserve the documented zero-config local workflow. Supported production
# deployment explicitly supplies DEBUG=0 and is validated fail-closed below.
DEBUG = os.environ.get("DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY and (DEBUG or TESTING):
    # Local/test-only key. Production fails closed below instead of silently
    # signing sessions and JWTs with a value published in the repository.
    SECRET_KEY = "django-insecure-local-development-only"

# Fail closed: production must not start with an absent or known development
# key. Environment variable names remain unchanged.
if not DEBUG and (
    len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or SECRET_KEY.startswith("django-insecure-")
):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY must be a strong, non-development value when DEBUG is off"
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
    "apps.rbac",
    "apps.employees",
    "apps.accounts",
    "apps.catalog",
    "apps.clients",
    "apps.eventlog",
    "apps.orders",
    "apps.warehouse",
    "apps.shipments",
    "apps.portal",
    "apps.notifications",
    "apps.cameras",
]

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
    # Прикладной throttling поверх nginx-лимитов: nginx режет по IP, DRF —
    # по пользователю и по дорогим/чувствительным эндпоинтам (логин, регистрация).
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
    },
    # nginx is the single trusted proxy in the supported deployment. Taking
    # the last forwarded address prevents a client-supplied first XFF value
    # from rotating the login/registration throttle identity.
    "NUM_PROXIES": int(os.environ.get("THROTTLE_NUM_PROXIES", "1")),
}

# Access and refresh tokens issued before a password reset must stop working.
# SimpleJWT embeds a one-way password-derived claim and checks it on every
# authenticated request; the refresh endpoint performs the same check below.
SIMPLE_JWT = {
    "CHECK_REVOKE_TOKEN": True,
}

if TESTING:
    # Пустые классы/ставки — троттлинг не мешает общим прогонам.
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()
    throttle_rates = cast(
        dict[str, object], REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    )
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        key: None for key in throttle_rates
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

_database_password = os.environ.get("DB_PASSWORD")
if _database_password is None:
    if not DEBUG:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("DB_PASSWORD must be set when DEBUG is off")
    _database_password = "asyl"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "asyl"),
        "USER": os.environ.get("DB_USER", "asyl"),
        "PASSWORD": _database_password,
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
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

# Время цеха: суточные отчёты и «окно оплаты по дням» считаются по местному
# календарю, а не по UTC (иначе утренние операции падают на вчерашний день).
TIME_ZONE = "Asia/Almaty"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

def _csv_setting(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]


# CORS — allow the Next.js frontend (separate origin) to call the API. Local
# origins are defaults only in debug mode; production must opt in explicitly.
CORS_ALLOWED_ORIGINS = _csv_setting(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000" if DEBUG else "",
)
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = _csv_setting("CSRF_TRUSTED_ORIGINS")

# Local development remains convenient, while production rejects an omitted or
# wildcard Host allowlist. The supported production compose supplies domains
# and the public server address explicitly.
_local_hosts = "localhost,127.0.0.1,[::1],testserver" if (DEBUG or TESTING) else ""
ALLOWED_HOSTS = _csv_setting("ALLOWED_HOSTS", _local_hosts)
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("ALLOWED_HOSTS must be explicit when DEBUG is off")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Ограничение размера тела запроса: заказы/оплаты — маленькие JSON, поэтому
# 5 МБ хватает и отсекает попытки залить гигантский payload в память.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", 5 * 1024 * 1024))
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

# Прод-хардеринг: включается, когда DEBUG выключен (за HTTPS-nginx).
# Локальная разработка по http остаётся рабочей.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    # HSTS дублирует nginx, но защищает и при прямом обращении к бэку.
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", 31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    # Редирект на HTTPS делает nginx; здесь не включаем, чтобы healthcheck по
    # http-loopback внутри сети не зациклился.

# Redis-кэш в проде (REDIS_URL из compose); локально/в тестах — память процесса.
if os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["REDIS_URL"],
        }
    }

# Реквизиты оплаты для клиентского портала (MVP — статичный Kaspi QR).
PORTAL_PAYMENT_INFO = {
    "kaspi_qr": os.environ.get("KASPI_QR", ""),  # URL картинки QR или payload-строка
    "bank": os.environ.get("PORTAL_BANK", "Kaspi Bank"),
    "account": os.environ.get("PORTAL_ACCOUNT", ""),
    "instructions": "Отсканируйте QR в приложении Kaspi и переведите сумму к оплате.",
}

# Реквизиты поставщика в PDF-счёте. В проде могут быть переопределены через env.
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
    # В предоставленном образце от 06.07.2026 НДС рассчитан по ставке 16%.
    "vat_rate": os.environ.get("INVOICE_VAT_RATE", "16"),
}
