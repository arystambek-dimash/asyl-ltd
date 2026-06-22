import redis
from django.conf import settings

_client = None


class CounterUnavailable(RuntimeError):
    pass


def get_client():
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL)
    return _client


def _key(camera_pk: int) -> str:
    return f"count:camera:{camera_pk}"


def _class_key(camera_pk: int) -> str:
    # хэш-разбивка по классам мешков: поле = класс (Red_50…), значение = счёт
    return f"count:camera:{camera_pk}:by_class"


def increment(camera_pk: int, by: int = 1, cls: str | None = None) -> int:
    """+by в общий счётчик и, если задан класс, в разбивку по классам."""
    try:
        client = get_client()
        total = int(client.incrby(_key(camera_pk), by))
        if cls:
            client.hincrby(_class_key(camera_pk), cls, by)
        return total
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def get(camera_pk: int) -> int:
    try:
        v = get_client().get(_key(camera_pk))
        return int(v) if v is not None else 0
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def get_breakdown(camera_pk: int) -> dict[str, int]:
    """Разбивка живого счёта по классам мешков: {"Red_50": 12, …}."""
    try:
        raw = get_client().hgetall(_class_key(camera_pk))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))
    out: dict[str, int] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, (bytes, bytearray)) else k
        try:
            out[key] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def reset(camera_pk: int) -> None:
    try:
        get_client().delete(_key(camera_pk), _class_key(camera_pk))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))
