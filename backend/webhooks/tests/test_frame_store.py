import pytest
import fakeredis
from webhooks import frame_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()  # bytes, decode_responses=False
    monkeypatch.setattr(frame_store, "_client", fake)


def test_put_get_roundtrip():
    assert frame_store.get(5) is None
    frame_store.put(5, b"\xff\xd8jpegbytes")
    assert frame_store.get(5) == b"\xff\xd8jpegbytes"


def test_put_overwrites():
    frame_store.put(5, b"first")
    frame_store.put(5, b"second")
    assert frame_store.get(5) == b"second"


def test_unavailable_raises(monkeypatch):
    class Boom:
        def set(self, *a, **k): raise __import__("redis").RedisError("down")
        def get(self, *a, **k): raise __import__("redis").RedisError("down")
    monkeypatch.setattr(frame_store, "_client", Boom())
    with pytest.raises(frame_store.FrameUnavailable):
        frame_store.put(1, b"x")
    with pytest.raises(frame_store.FrameUnavailable):
        frame_store.get(1)
