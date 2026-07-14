import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Под pytest троттлинг выключаем: общие фикстуры-юзеры делают много запросов
# и упирались бы в лимиты. Прицельные тесты троттла включают его локально.
TESTING = "pytest" in sys.modules or os.environ.get("PYTEST_RUNNING") == "1"

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-n^--vbbev=3i(v4ztl5w(nm4ym3uw4ow9ozx=))e+7b165k(8$",
)
DEBUG = os.environ.get("DEBUG", "1") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
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
        "burst": os.environ.get("THROTTLE_BURST", "30/sec"),
        "login": os.environ.get("THROTTLE_LOGIN", "10/min"),
        "register": os.environ.get("THROTTLE_REGISTER", "5/min"),
    },
}

if TESTING:
    # Пустые классы/ставки — троттлинг не мешает общим прогонам.
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        k: None for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
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

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS — allow the Next.js frontend (separate origin) to call the API.
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    origin
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin
]

# Allowed hosts. On an on-prem shop LAN the server is reached by its local IP,
# so the default allows any host. Override ALLOWED_HOSTS in production behind a
# domain.
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

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
