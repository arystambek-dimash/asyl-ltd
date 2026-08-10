from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle


class _FixedScopeThrottle(SimpleRateThrottle):
    def get_rate(self):
        return api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)

    def get_cache_key(self, request, view):
        if self.rate is None:
            return None  # ставка не задана — троттл не применяется
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),  # по IP: логин/регистрация анонимны
        }


class LoginRateThrottle(_FixedScopeThrottle):
    scope = "login"


class RegisterRateThrottle(_FixedScopeThrottle):
    scope = "register"


class PortalOrderCreateRateThrottle(_FixedScopeThrottle):
    """Per-account limit for the portal's write-heavy order creation path."""

    scope = "portal_order_create"

    def get_cache_key(self, request, view):
        if self.rate is None:
            return None
        user = getattr(request, "user", None)
        ident = (
            f"user-{user.pk}"
            if user is not None and user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}
