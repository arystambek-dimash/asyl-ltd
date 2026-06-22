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


def increment(camera_pk: int, by: int = 1) -> int:
    try:
        return int(get_client().incrby(_key(camera_pk), by))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def get(camera_pk: int) -> int:
    try:
        v = get_client().get(_key(camera_pk))
        return int(v) if v is not None else 0
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def reset(camera_pk: int) -> None:
    try:
        get_client().delete(_key(camera_pk))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))
