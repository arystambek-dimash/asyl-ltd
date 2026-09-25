from uuid import UUID, uuid4

import pytest

from apps.common.uuids import parse_canonical_uuid


def test_parse_canonical_uuid_returns_the_uuid():
    value = uuid4()
    assert parse_canonical_uuid(str(value)) == value
    assert isinstance(parse_canonical_uuid(str(value)), UUID)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        123,
        "",
        "not-a-uuid",
        str(uuid4()).upper(),
        "{" + str(uuid4()) + "}",
        uuid4().hex,
    ],
)
def test_parse_canonical_uuid_rejects_non_canonical_values(raw):
    assert parse_canonical_uuid(raw) is None
