import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-n^--vbbev=3i(v4ztl5w(nm4ym3uw4ow9ozx=))e+7b165k(8$"
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
]

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
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

STATIC_URL = "static/"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS — allow the Next.js frontend (separate origin) to call the API.
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000"
).split(",")
CORS_ALLOW_CREDENTIALS = True

# Allowed hosts. On an on-prem shop LAN the server is reached by its local IP,
# so the default allows any host. Override ALLOWED_HOSTS in production behind a
# domain.
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# Реквизиты оплаты для клиентского портала (MVP — статичный Kaspi QR).
PORTAL_PAYMENT_INFO = {
    "kaspi_qr": os.environ.get("KASPI_QR", ""),  # URL картинки QR или payload-строка
    "bank": os.environ.get("PORTAL_BANK", "Kaspi Bank"),
    "account": os.environ.get("PORTAL_ACCOUNT", ""),
    "instructions": "Отсканируйте QR в приложении Kaspi и переведите сумму к оплате.",
}
