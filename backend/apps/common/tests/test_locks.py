from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.db import connection, connections, transaction

from apps.common.locks import (
    advisory_lock,
    claim_owned_lease,
    refresh_owned_lease,
    release_owned_lease,
)

NAMESPACE = 0x544553  # "TES"


def _held_advisory_locks() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        )
        return cursor.fetchone()[0]


@pytest.mark.django_db
@pytest.mark.parametrize("key", [(NAMESPACE, 7), (0x5445535453,)])
def test_advisory_lock_is_released_after_the_block(key):
    with advisory_lock(*key) as acquired:
        assert acquired is True
        assert _held_advisory_locks() == 1

    assert _held_advisory_locks() == 0


@pytest.mark.django_db(transaction=True)
def test_try_advisory_lock_reports_contention_without_waiting():
    entered, release = Event(), Event()

    def hold():
        try:
            with advisory_lock(NAMESPACE, 8):
                entered.set()
                assert release.wait(timeout=10)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        try:
            assert entered.wait(timeout=10)
            with advisory_lock(NAMESPACE, 8, blocking=False) as acquired:
                assert acquired is False
        finally:
            release.set()
        future.result(timeout=10)

    with advisory_lock(NAMESPACE, 8, blocking=False) as acquired:
        assert acquired is True


@pytest.mark.django_db(transaction=True)
def test_advisory_lock_in_aborted_transaction_closes_the_session():
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            with advisory_lock(NAMESPACE, 9):
                # Как после ошибки SQL внутри atomic: unlock уже не выполнить.
                connection.needs_rollback = True
                raise RuntimeError("boom")

    # Сессию закрыли вместо unlock — PostgreSQL снял лок вместе с ней.
    assert _held_advisory_locks() == 0


def _redis_backend(client):
    serializer = Mock()
    serializer.dumps.return_value = b"encoded-owner"
    adapter = SimpleNamespace(
        get_client=Mock(return_value=client),
        _serializer=serializer,
    )
    return SimpleNamespace(
        _cache=adapter,
        add=Mock(return_value=True),
        make_and_validate_key=Mock(return_value=":1:lease"),
    )


def test_redis_owned_lease_is_owner_atomic():
    client = Mock()
    client.eval.side_effect = [1, 0]
    backend = _redis_backend(client)

    with patch("apps.common.locks.cache", backend):
        assert claim_owned_lease("lease", "owner", 90) is True
        assert refresh_owned_lease("lease", "owner", 90) is True
        release_owned_lease("lease", "owner")

    backend.add.assert_called_once_with("lease", "owner", timeout=90)
    first = client.eval.call_args_list[0].args
    assert first[1:] == (1, ":1:lease", b"encoded-owner", 90)
    second = client.eval.call_args_list[1].args
    assert second[1:] == (1, ":1:lease", b"encoded-owner")


def test_owned_lease_release_swallows_cache_outage():
    client = Mock()
    client.eval.side_effect = ConnectionError("redis down")

    with patch("apps.common.locks.cache", _redis_backend(client)):
        release_owned_lease("lease", "owner")

    client.eval.assert_called_once()


def test_local_owned_lease_never_touches_another_owner():
    assert claim_owned_lease("lease", "new-owner", 30) is True
    assert claim_owned_lease("lease", "stale-owner", 30) is False

    assert refresh_owned_lease("lease", "stale-owner", 90) is False
    release_owned_lease("lease", "stale-owner")
    assert cache.get("lease") == "new-owner"

    assert refresh_owned_lease("lease", "new-owner", 90) is True
    release_owned_lease("lease", "new-owner")
    assert cache.get("lease") is None
