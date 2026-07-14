"""Точечные лимиты на чувствительные эндпоинты поверх глобальных anon/user.

Логин и регистрация — главные цели брутфорса и авто-регистраций. nginx уже
режет по IP на входе; эти классы добавляют защиту на уровне приложения
(работают через общий кэш — Redis в проде), в т.ч. когда трафик приходит
из-за прокси/CDN с общего IP или через несколько путей.

Используем фиксированный scope на самом классе (не через throttle_scope на
вью), чтобы лимит действовал независимо от того, у какого вью он навешан.
"""
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle


class _FixedScopeThrottle(SimpleRateThrottle):
    def get_rate(self):
        # Читаем ставку из живых настроек, а не из class-level THROTTLE_RATES,
        # который «замораживается» на импорте (важно для override_settings).
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
