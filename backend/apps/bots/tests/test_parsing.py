"""Разбор отчёта об отгрузке вагонов: пример владельца, допуски и поломки."""

from datetime import date
from decimal import Decimal

import pytest

from apps.bots.parsing import bags_for, format_rail_report, parse_rail_report, wagon_number_status
from apps.bots.tests.samples import OWNER_REPORT, OWNER_WAGONS, issue_codes, report

VALID = OWNER_WAGONS[0]


def test_owner_sample_parses_into_twelve_wagons():
    parsed = parse_rail_report(OWNER_REPORT)

    assert parsed.issues == ()
    assert parsed.ok
    assert parsed.day == date(2026, 9, 19)
    assert parsed.country == "Узбекистан"
    assert parsed.client_name == "ООО OSIYO NAV NIHOL"
    assert parsed.station == "Раустан"
    assert parsed.declared_wagons == 12
    assert [wagon.number for wagon in parsed.wagons] == list(OWNER_WAGONS)
    assert [wagon.position for wagon in parsed.wagons] == list(range(1, 13))
    assert {wagon.code for wagon in parsed.wagons} == {"Д1с"}
    assert {wagon.tons for wagon in parsed.wagons} == {Decimal("68")}
    assert parsed.total_tons == Decimal("816")
    # Строка сообщения: шапка — 1, станция — 2, вагоны с 3-й.
    assert parsed.wagons[0].line == 3


def test_owner_sample_is_16320_bags_of_50_kg():
    parsed = parse_rail_report(OWNER_REPORT)

    bags = [bags_for(wagon.weight_kg, Decimal("50")) for wagon in parsed.wagons]

    assert parsed.wagons[0].weight_kg == Decimal("68000")
    assert set(bags) == {1360}
    assert sum(bags) == 16320


@pytest.mark.parametrize(
    "line",
    [
        f"Д1с-{VALID}-68 тн",
        f"Д1с\u00a0-\u00a0{VALID}\u00a0-\u00a068\u00a0тн",  # неразрывные пробелы из WhatsApp
        f"Д1с–{VALID}–68 тн",  # короткое тире
        f"Д1с—{VALID}—68 тн",  # длинное тире
        f"Д1с - {VALID} - 68 тн",
        f"Д1c-{VALID}-68 тн",  # латинская «c»
        f"Д1С-{VALID}-68 ТН",
        f"Д1с-{VALID}-68 т",
        f"Д1с-{VALID}-68т",
        f"Д1с-{VALID}-68тн.",
        f"Д1с-{VALID}-68",
        f"  Д1с-{VALID}-68 тн  ",
    ],
)
def test_wagon_line_tolerances(line):
    parsed = parse_rail_report(report(line))

    assert parsed.issues == ()
    (wagon,) = parsed.wagons
    assert wagon.number == VALID
    assert wagon.tons == Decimal("68")
    assert wagon.code_key == parse_rail_report(report(f"Д1с-{VALID}-68 тн")).wagons[0].code_key


def test_decimal_comma_tons():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-67,5 тн", "Д1с-28087666-67.5 тн"))

    assert parsed.issues == ()
    assert [wagon.tons for wagon in parsed.wagons] == [Decimal("67.5"), Decimal("67.5")]
    assert bags_for(parsed.wagons[0].weight_kg, Decimal("50")) == 1350


@pytest.mark.parametrize(
    ("header", "day", "country", "client"),
    [
        ("19.09.26 Узбекистан ООО OSIYO NAV NIHOL", date(2026, 9, 19), "Узбекистан", "ООО OSIYO NAV NIHOL"),
        ("Сб. 19.09.2026 Узбекистан  ООО OSIYO NAV NIHOL", date(2026, 9, 19), "Узбекистан", "ООО OSIYO NAV NIHOL"),
        ("суббота 19.09.26 узбекистан ООО OSIYO", date(2026, 9, 19), "Узбекистан", "ООО OSIYO"),
        ("пн 1.9.26 Кыргызстан ОсОО Ала-Тоо", date(2026, 9, 1), "Кыргызстан", "ОсОО Ала-Тоо"),
        # Страны нет в списке — вся строка после даты считается клиентом.
        ("сб 19.09.26 ТОО Асыл Агро", date(2026, 9, 19), "", "ТОО Асыл Агро"),
    ],
)
def test_header_variants(header, day, country, client):
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", header=header))

    assert parsed.issues == ()
    assert (parsed.day, parsed.country, parsed.client_name) == (day, country, client)


@pytest.mark.parametrize(
    ("station_line", "station", "declared"),
    [
        ("Ст.Раустан 1 вагон", "Раустан", 1),
        ("ст. Сары-Агаш 1 вагона", "Сары-Агаш", 1),
        ("Станция Раустан 1 вагонов", "Раустан", 1),
        # Разделитель перед числом вагонов не попадает в станцию (накладная, уведомление).
        ("Ст. Раустан - 1 вагон", "Раустан", 1),
        ("Ст. Раустан: 1 вагон", "Раустан", 1),
        ("Ст. Раустан — 1 вагон", "Раустан", 1),
    ],
)
def test_station_variants(station_line, station, declared):
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", station=station_line))

    assert parsed.issues == ()
    assert (parsed.station, parsed.declared_wagons) == (station, declared)


def test_blank_lines_and_windows_line_endings_are_ignored():
    lines = report(f"Д1с-{VALID}-68 тн  ", "", "Д1с-28087666-68 тн", station="Ст. Раустан 2 вагона")
    text = "\r\n\r\n" + lines.replace("\n", "\r\n") + "\r\n\r\n"

    parsed = parse_rail_report(text)

    assert parsed.issues == ()
    assert [wagon.number for wagon in parsed.wagons] == [VALID, "28087666"]


def test_declared_count_must_match_wagon_lines():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", station="Ст. Раустан 12 вагон"))

    assert issue_codes(parsed) == ["wagon_count_mismatch"]
    assert "12" in parsed.issues[0].message and "1" in parsed.issues[0].message
    assert not parsed.ok


def test_missing_wagon_count_is_an_issue():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", station="Ст. Раустан"))

    assert parsed.station == "Раустан"
    assert issue_codes(parsed) == ["wagon_count_missing"]


def test_duplicate_wagon_number():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", f"Д1с-{VALID}-68 тн"))

    assert issue_codes(parsed) == ["duplicate_wagon"]
    assert parsed.issues[0].subject == VALID
    assert parsed.issues[0].line == 4


def test_wagon_check_digit_is_verified():
    parsed = parse_rail_report(report("Д1с-28087659-68 тн"))

    assert issue_codes(parsed) == ["wagon_check_digit"]
    assert parsed.issues[0].subject == "28087659"


@pytest.mark.parametrize("tons", ["680", "100000", "75,5"])
def test_more_tons_than_a_wagon_holds_is_a_typo(tons):
    """«680 тн» вместо «68 тн» бот не проводит: 13 600 мешков со склада и в долг."""
    parsed = parse_rail_report(report(f"Д1с-{VALID}-{tons} тн"))

    assert issue_codes(parsed) == ["bad_tons"]
    assert parsed.issues[0].message == (
        f"Вагон {VALID}: {tons} т — больше, чем помещается в вагон (до 75 т)")
    assert (parsed.issues[0].line, parsed.issues[0].subject) == (3, VALID)


def test_full_75_ton_wagon_is_fine():
    assert parse_rail_report(report(f"Д1с-{VALID}-75 тн")).ok


def test_unknown_line_goes_to_review():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", "Итого 68 тонн"))

    assert "unknown_line" in issue_codes(parsed)
    unknown = next(issue for issue in parsed.issues if issue.code == "unknown_line")
    assert unknown.line == 4
    assert "Итого 68 тонн" in unknown.message


def test_missing_header_and_station():
    parsed = parse_rail_report(f"Д1с-{VALID}-68 тн")

    assert issue_codes(parsed) == ["header_missing", "station_missing"]
    assert parsed.day is None and parsed.client_name == ""


def test_impossible_date():
    parsed = parse_rail_report(report(f"Д1с-{VALID}-68 тн", header="сб 31.02.26 Узбекистан ООО OSIYO"))

    assert issue_codes(parsed) == ["bad_date"]
    assert parsed.day is None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (report("Д1с-2808765-68 тн"), "wagon_number_length"),
        (report(f"Д1с-{VALID}-0 тн"), "bad_tons"),
        (report(f"Д1с-{VALID}-68 тн", header="сб 19.09.26 Узбекистан"), "client_missing"),
        (report(station="Ст. Раустан 12 вагон"), "no_wagons"),
    ],
)
def test_one_broken_part_is_one_issue(text, code):
    assert issue_codes(parse_rail_report(text)) == [code]


def test_second_report_in_one_message_goes_to_review():
    text = OWNER_REPORT + "\n" + report(f"Д1с-{VALID}-68 тн", header="вс 20.09.26 Узбекистан ООО Другой")

    parsed = parse_rail_report(text)

    assert "several_reports" in issue_codes(parsed)
    assert parsed.client_name == "ООО OSIYO NAV NIHOL"


def test_empty_message():
    parsed = parse_rail_report("  \n ")

    assert "header_missing" in issue_codes(parsed)
    assert not parsed.ok


@pytest.mark.parametrize(
    ("weight_kg", "bag_kg", "bags"),
    [
        (Decimal("68000"), Decimal("50"), 1360),
        (Decimal("67500"), Decimal("50"), 1350),
        (Decimal("68010"), Decimal("50"), None),  # 68,01 т не делится на мешки по 50 кг
        (Decimal("68000"), Decimal("0"), None),
        (Decimal("0"), Decimal("50"), None),
    ],
)
def test_bags_for(weight_kg, bag_kg, bags):
    assert bags_for(weight_kg, bag_kg) == bags


@pytest.mark.parametrize(
    ("number", "status"),
    [(VALID, "ok"), ("28087659", "check_digit"), ("2808765", "length"), ("280876580", "length")],
)
def test_wagon_number_status(number, status):
    assert wagon_number_status(number) == status


def test_owner_format_round_trips_through_the_parser():
    text = format_rail_report(
        day=date(2026, 9, 19),
        country="Узбекистан",
        client_name="ООО OSIYO NAV NIHOL",
        station="Раустан",
        wagons=[("Д1с", number, Decimal("68.000")) for number in OWNER_WAGONS[:2]] + [("Б2", OWNER_WAGONS[2], Decimal("67.5"))],
    )

    assert text.splitlines() == [
        "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. Раустан 3 вагон",
        f"Д1с-{OWNER_WAGONS[0]}-68 тн",
        f"Д1с-{OWNER_WAGONS[1]}-68 тн",
        f"Б2-{OWNER_WAGONS[2]}-67,5 тн",
    ]
    parsed = parse_rail_report(text)
    assert parsed.ok
    assert (parsed.day, parsed.country, parsed.client_name, parsed.station) == (
        date(2026, 9, 19), "Узбекистан", "ООО OSIYO NAV NIHOL", "Раустан")
    assert [(wagon.code, wagon.number, wagon.tons) for wagon in parsed.wagons] == [
        ("Д1с", OWNER_WAGONS[0], Decimal("68")),
        ("Д1с", OWNER_WAGONS[1], Decimal("68")),
        ("Б2", OWNER_WAGONS[2], Decimal("67.5")),
    ]


def test_owner_format_drops_a_country_the_header_does_not_know():
    text = format_rail_report(
        day=date(2026, 9, 21), country="Другая", client_name="ТОО Ромашка", station="", wagons=[("Д1с", VALID, Decimal("68"))],
    )

    assert text.splitlines()[:2] == ["пн 21.09.26 ТОО Ромашка", "Ст. 1 вагон"]
    parsed = parse_rail_report(text)
    assert (parsed.country, parsed.client_name, parsed.declared_wagons) == ("", "ТОО Ромашка", 1)
