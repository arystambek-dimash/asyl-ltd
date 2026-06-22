import pytest
import fakeredis
from webhooks import counter_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(counter_store, "_client", fake)
    return fake


def test_increment_and_get():
    assert counter_store.get(7) == 0
    assert counter_store.increment(7) == 1
    assert counter_store.increment(7, by=3) == 4
    assert counter_store.get(7) == 4


def test_reset():
    counter_store.increment(7, by=5)
    counter_store.reset(7)
    assert counter_store.get(7) == 0
