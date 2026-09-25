"""Межпроцессные блокировки: PostgreSQL advisory lock и владельческая Redis-аренда.

Advisory lock — сессионный: он переживает короткие транзакции внутри
защищённого участка и снимается самим PostgreSQL, если соединение воркера
умерло. Ключ — один bigint или пара int4 ``(namespace, id)``, как у
``pg_advisory_lock``; пространства ключей держите именованными константами
рядом с местом использования.

Владельческая аренда — ключ кэша с TTL, значение которого — владелец. В Redis
снятие и продление атомарно сравнивают владельца Lua-скриптом, поэтому
просроченный воркер не удалит и не продлит аренду, которую уже взял другой.
Без Redis кэш — процессный LocMemCache (локальный запуск, тесты): там
захват, сравнение и удаление идут под одним ``threading.Lock``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from django.core.cache import cache
from django.db import DatabaseError, connection

log = logging.getLogger(__name__)
_LOCAL_LEASE_GUARD = Lock()

_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_COMPARE_AND_EXPIRE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


def _key_placeholders(key: tuple[int, ...]) -> str:
    if len(key) not in (1, 2):
        raise ValueError("Advisory lock key is one bigint or two int4 values")
    return ", ".join(["%s"] * len(key))


def acquire_advisory_lock(*key: int, blocking: bool = True) -> bool:
    """Взять сессионный advisory lock; ``blocking=False`` — не ждать занятый."""

    function = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}({_key_placeholders(key)})", list(key))
        row = cursor.fetchone()
    return blocking or row == (True,)


def release_advisory_lock(*key: int) -> None:
    """Снять advisory lock, при любом сбое — закрыть сессию (fail-closed)."""

    if connection.connection is None:
        # Сессию уже закрыли — PostgreSQL снял все её advisory-локи.
        return
    if connection.needs_rollback:
        # В прерванной транзакции unlock не выполнить. Закрытие сессии снимает
        # лок и не подменяет исходную ошибку новой.
        connection.close()
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT pg_advisory_unlock({_key_placeholders(key)})", list(key)
            )
            row = cursor.fetchone()
    except DatabaseError:
        log.exception("Could not release PostgreSQL advisory lock %s", key)
        connection.close()
        return
    if row != (True,):
        log.error("PostgreSQL advisory lock %s was not owned at release", key)


@contextmanager
def advisory_lock(*key: int, blocking: bool = True) -> Iterator[bool]:
    """Держать advisory lock на время блока; отдаёт, удалось ли его взять."""

    acquired = acquire_advisory_lock(*key, blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            release_advisory_lock(*key)


def _redis_adapter():
    adapter = getattr(cache, "_cache", None)
    if not callable(getattr(adapter, "get_client", None)):
        return None
    if getattr(adapter, "_serializer", None) is None:
        return None
    return adapter


def _redis_owned_eval(adapter, script: str, key: str, owner: str, *args) -> bool:
    raw_key = cache.make_and_validate_key(key)
    client = adapter.get_client(raw_key, write=True)
    return bool(
        client.eval(script, 1, raw_key, adapter._serializer.dumps(owner), *args)
    )


def claim_owned_lease(key: str, owner: str, timeout: int) -> bool:
    """Взять аренду на ``timeout`` секунд, если ключ свободен."""

    if _redis_adapter() is not None:
        return bool(cache.add(key, owner, timeout=timeout))
    with _LOCAL_LEASE_GUARD:
        return bool(cache.add(key, owner, timeout=timeout))


def refresh_owned_lease(key: str, owner: str, timeout: int) -> bool:
    """Продлить аренду на ``timeout`` секунд, только если она у ``owner``."""

    adapter = _redis_adapter()
    if adapter is not None:
        return _redis_owned_eval(
            adapter, _COMPARE_AND_EXPIRE, key, owner, max(1, int(timeout))
        )
    with _LOCAL_LEASE_GUARD:
        if cache.get(key) != owner:
            return False
        cache.set(key, owner, timeout=timeout)
        return True


def release_owned_lease(key: str, owner: str) -> None:
    """Снять аренду, только если она у ``owner``; сбой кэша не пробрасывается.

    Снятие — последний шаг после уже сделанной работы: при сбое кэша аренда
    всё равно истечёт по TTL, а исходный результат или исключение не должны
    подменяться ошибкой снятия.
    """

    try:
        adapter = _redis_adapter()
        if adapter is not None:
            _redis_owned_eval(adapter, _COMPARE_AND_DELETE, key, owner)
            return
        with _LOCAL_LEASE_GUARD:
            if cache.get(key) == owner:
                cache.delete(key)
    except Exception:
        log.exception("Could not release owned lease %s", key)
