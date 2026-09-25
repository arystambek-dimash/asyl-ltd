from datetime import UTC, datetime, timedelta

import pytest

from apps.common.datetimes import parse_aware_datetime


def test_parse_aware_datetime_keeps_the_offset():
    parsed = parse_aware_datetime("2026-09-01T10:00:00+05:00")
    assert parsed == datetime(2026, 9, 1, 5, 0, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(hours=5)
    assert parse_aware_datetime("2026-09-01T05:00:00Z") == datetime(2026, 9, 1, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        1_725_000_000,
        "",
        "вчера",
        # Без пояса время неоднозначно.
        "2026-09-01T10:00:00",
        # Правильно оформленная, но несуществующая дата: parse_datetime бросает ValueError.
        "2026-02-30T10:00:00+05:00",
    ],
)
def test_parse_aware_datetime_rejects_everything_else(raw):
    assert parse_aware_datetime(raw) is None
