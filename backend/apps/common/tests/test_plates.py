import json
from pathlib import Path

import pytest
from rest_framework.exceptions import ValidationError

from apps.common.plates import (
    clean_plate,
    detect_plate_country,
    format_plate,
    format_plate_pair,
    normalize_plate,
    plate_match_key,
    plate_search_variants,
    plate_warning,
)

# Те же векторы проверяет фронтенд (lib/plates.ts): правила должны совпадать.
VECTORS = json.loads((Path(__file__).parent / "plate_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("raw", "expected"), VECTORS["normalize"])
def test_normalize_plate(raw, expected):
    assert normalize_plate(raw) == expected


def test_normalize_plate_accepts_none():
    assert normalize_plate(None) == ""


@pytest.mark.parametrize(("compact", "expected"), VECTORS["match_key"])
def test_plate_match_key(compact, expected):
    assert plate_match_key(compact) == expected


@pytest.mark.parametrize(("compact", "expected"), VECTORS["country"])
def test_detect_plate_country(compact, expected):
    assert detect_plate_country(compact) == expected


@pytest.mark.parametrize(("raw", "expected"), VECTORS["format"])
def test_format_plate(raw, expected):
    assert format_plate(raw) == expected


@pytest.mark.parametrize("raw", VECTORS["valid"])
def test_clean_plate_accepts_any_country(raw):
    assert clean_plate(raw) == normalize_plate(raw)


@pytest.mark.parametrize("raw", VECTORS["invalid"])
def test_clean_plate_rejects_what_cannot_be_a_plate(raw):
    with pytest.raises(ValidationError) as exc:
        clean_plate(raw, field="trailer_number")
    assert "trailer_number" in exc.value.detail


def test_clean_plate_keeps_blank_as_no_number():
    assert clean_plate("  ") == ""


@pytest.mark.parametrize(("compact", "warned"), VECTORS["truck_warning"])
def test_truck_warning_is_soft(compact, warned):
    assert (plate_warning(compact) is not None) is warned


@pytest.mark.parametrize(("compact", "warned"), VECTORS["trailer_warning"])
def test_trailer_warning_is_soft(compact, warned):
    assert (plate_warning(compact, kind="trailer") is not None) is warned


@pytest.mark.parametrize(("search", "expected"), VECTORS["search_variants"])
def test_plate_search_variants(search, expected):
    assert plate_search_variants(search) == expected


def test_format_plate_pair_joins_trailer():
    assert format_plate_pair("07KG695ADT", "07KG837PB") == "07 KG 695 ADT / 07 KG 837 PB"
    assert format_plate_pair("403BJN13", "") == "403 BJN 13"
    assert format_plate_pair("", "") == ""
