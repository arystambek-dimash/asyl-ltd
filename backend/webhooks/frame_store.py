import redis
from django.conf import settings

_client = None
TTL_SECONDS = 10


class FrameUnavailable(RuntimeError):
    pass


def get_client():
    global _client
    if _client is None:
        # decode_responses=False — нам нужны сырые JPEG-байты, не строки.
        _client = redis.from_url(settings.REDIS_URL, decode_responses=False)
    return _client


def _key(job_id: int) -> str:
    return f"frame:job:{job_id}"


def put(job_id: int, jpeg: bytes) -> None:
    try:
        get_client().set(_key(job_id), jpeg, ex=TTL_SECONDS)
    except redis.RedisError as e:
        raise FrameUnavailable(str(e))


def get(job_id: int):
    try:
        return get_client().get(_key(job_id))
    except redis.RedisError as e:
        raise FrameUnavailable(str(e))
