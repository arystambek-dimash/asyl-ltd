from django.contrib import admin
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

# Учёт refresh-токенов сессий (credentials.SessionRefreshToken) — служебный,
# в админке ему делать нечего. token_blacklist стоит в INSTALLED_APPS раньше
# apps.accounts, поэтому его admin.py уже зарегистрировал модели.
admin.site.unregister(OutstandingToken)
admin.site.unregister(BlacklistedToken)
