import pytest
from rest_framework.exceptions import ValidationError

from apps.common.pagination import keyset_page, parse_pk_cursor
from apps.eventlog.models import EventLog


@pytest.mark.parametrize("raw", [None, ""])
def test_parse_pk_cursor_accepts_an_omitted_cursor(raw):
    assert parse_pk_cursor(raw) is None


def test_parse_pk_cursor_returns_the_pk():
    assert parse_pk_cursor("42") == 42


@pytest.mark.parametrize("raw", ["x", "0", "-1", "1.5", "²", " 7", "1_0"])
def test_parse_pk_cursor_rejects_anything_but_a_positive_integer(raw):
    with pytest.raises(ValidationError):
        parse_pk_cursor(raw)


@pytest.mark.django_db
def test_keyset_page_walks_the_rows_by_descending_pk():
    for index in range(5):
        EventLog.objects.create(event_type="test", message=str(index))
    rows = EventLog.objects.order_by("-id")
    ids = list(rows.values_list("id", flat=True))

    page, next_cursor = keyset_page(rows, None, size=2)
    assert [row.pk for row in page] == ids[:2] and next_cursor == ids[1]
    page, next_cursor = keyset_page(rows, str(next_cursor), size=2)
    assert [row.pk for row in page] == ids[2:4] and next_cursor == ids[3]
    page, next_cursor = keyset_page(rows, str(next_cursor), size=2)
    assert [row.pk for row in page] == ids[4:] and next_cursor is None
