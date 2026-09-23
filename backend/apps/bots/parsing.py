"""Отчёт об отгрузке вагонов (Джин-Син → группа «Отгрузка вагонов»).

Формат владельца::

    сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL
    Ст. Раустан 12 вагон
    Д1с-28087658-68 тн
    …

Шапка — день недели (необязательно), дата, страна из списка и клиент.
Станция — «Ст. X N вагон(а/ов)». Строка вагона — «[код товара]-[8 цифр
вагона]-[тонны] тн». Допуски: неразрывные пробелы, тире «–» и «—», латинская
«c» вместо кириллической «с», «т»/«тн», десятичная запятая. Вагон тяжелее
:data:`MAX_WAGON_TONS` — опечатка («680 тн» вместо «68 тн»), тоже на разбор.

Разбор чистый — без базы и часов. Всё, что не сошлось, попадает в ``issues``,
и такой отчёт уходит на разбор человеку. Клиента, товары, цены, мешки и дубли
проверяет :func:`apps.bots.rail.resolve_report`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from apps.cameras.shipping_segment_identity import valid_number
from apps.common.text import match_key

# Страны шапки — как в карточке клиента (frontend/src/lib/countries.ts).
REPORT_COUNTRIES = (
    "Казахстан", "Узбекистан", "Афганистан", "Кыргызстан", "Таджикистан", "Туркменистан",
    "Китай", "Иран", "Россия", "Азербайджан", "Грузия", "Монголия",
)
WAGON_NUMBER_LENGTH = 8
KG_PER_TON = Decimal("1000")
# Грузоподъёмность самых больших вагонов СНГ (полувагон) — 75 т; у владельца
# вагоны по 68 т. Больше — опечатка в отчёте: бот проводит сам, и «680 тн»
# списали бы склад и записали долг за 13 600 мешков.
MAX_WAGON_TONS = Decimal("75")
# Разделители между частями строки: «Узбекистан — ООО …», «Ст. Раустан: 12 вагон».
_SEPARATORS = " ,;:-\u2013\u2014"

# Дефис, неразрывный дефис, короткое и длинное тире.
_DASH = r"\s?[-\u2010\u2011\u2013\u2014]\s?"
_WAGON_RE = re.compile(
    rf"(?P<code>\S.*?){_DASH}(?P<number>\d{{5,12}}){_DASH}(?P<tons>\d+(?:[.,]\d+)?)\s?(?:тн|т|tn|t)?\.?",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"(?:[а-яё]+\.?,?\s)?(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4}|\d{2})(?:\s(?P<rest>.*))?",
    re.IGNORECASE,
)
_STATION_RE = re.compile(
    # «\s??» — лениво: «Ст. 12 вагон» без названия — пустая станция, а не станция «12 вагон».
    r"(?:ст\.|ст(?=\s)|станция(?=\s))\s??(?P<station>.*?)(?:,?\s(?P<count>\d+)\s?ваг[оа]н\w*\.?)?",
    re.IGNORECASE,
)
_QUOTE_LIMIT = 80
_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


@dataclass(frozen=True)
class ReportIssue:
    """Почему отчёт нельзя провести без человека."""

    code: str
    message: str
    # Номер строки сообщения (с 1), если причина в конкретной строке.
    line: int | None = None
    # Что именно не распознано: номер вагона, код товара, название клиента.
    subject: str = ""
    # Заказ, из-за которого отчёт похож на дубль (заполняет resolve_report).
    order_id: int | None = None


@dataclass(frozen=True)
class WagonLine:
    position: int  # порядок вагона в отчёте, с 1
    line: int  # номер строки сообщения
    code: str  # код товара как в отчёте: «Д1с»
    code_key: str  # ключ словаря товаров (catalog.ProductAlias.code)
    number: str
    tons: Decimal

    @property
    def weight_kg(self) -> Decimal:
        return self.tons * KG_PER_TON


@dataclass(frozen=True)
class RailReport:
    day: date | None
    country: str
    client_name: str
    station: str
    declared_wagons: int | None
    wagons: tuple[WagonLine, ...]
    issues: tuple[ReportIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def total_tons(self) -> Decimal:
        return sum((wagon.tons for wagon in self.wagons), Decimal("0"))


def bags_for(weight_kg: Decimal, bag_weight_kg: Decimal) -> int | None:
    """Мешков в вагоне: вес / фасовка. Не делится нацело — ``None`` (на разбор)."""
    if weight_kg <= 0 or bag_weight_kg <= 0:
        return None
    bags, rest = divmod(weight_kg, bag_weight_kg)
    return int(bags) if rest == 0 else None


def format_tons(value: Decimal) -> str:
    """«68», «67,5» — тонны без хвостовых нулей, с запятой."""
    return format(value.normalize(), "f").replace(".", ",")


def _quote(line: str) -> str:
    return line if len(line) <= _QUOTE_LIMIT else line[: _QUOTE_LIMIT - 1] + "…"


def _split_country(rest: str) -> tuple[str, str]:
    """«Узбекистан  ООО OSIYO» → («Узбекистан», «ООО OSIYO»); нет страны — («», всё)."""
    folded = rest.casefold()
    for country in REPORT_COUNTRIES:
        name = country.casefold()
        if folded.startswith(name) and not folded[len(name): len(name) + 1].isalpha():
            return country, rest[len(country):].strip(_SEPARATORS)
    return "", rest


def _header_day(match: re.Match) -> date | None:
    year = int(match["year"])
    try:
        return date(year + 2000 if year < 100 else year, int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def wagon_number_status(number: str) -> str:
    """«ok», «length» (не 8 цифр) или «check_digit» (не сходится контрольная цифра)."""
    if len(number) != WAGON_NUMBER_LENGTH or not number.isdigit():
        return "length"
    return "ok" if valid_number(number, "wagon_number") else "check_digit"


def _wagon_issues(wagon: WagonLine, seen: set[str]) -> list[ReportIssue]:
    number = wagon.number
    issues = []
    status = wagon_number_status(number)
    if status == "length":
        issues.append(ReportIssue(
            "wagon_number_length", f"Вагон {number}: номер должен содержать 8 цифр", wagon.line, number))
    elif status == "check_digit":
        issues.append(ReportIssue(
            "wagon_check_digit", f"Вагон {number}: номер с ошибкой — не сходится контрольная цифра",
            wagon.line, number))
    if number in seen:
        issues.append(ReportIssue("duplicate_wagon", f"Вагон {number} указан дважды", wagon.line, number))
    if wagon.tons <= 0:
        issues.append(ReportIssue("bad_tons", f"Вагон {number}: вес должен быть больше нуля", wagon.line, number))
    elif wagon.tons > MAX_WAGON_TONS:
        issues.append(ReportIssue(
            "bad_tons",
            f"Вагон {number}: {format_tons(wagon.tons)} т — больше, чем помещается в вагон "
            f"(до {format_tons(MAX_WAGON_TONS)} т)",
            wagon.line, number))
    return issues


def parse_rail_report(text) -> RailReport:
    """Разобрать отчёт; то, что не сошлось, — в ``issues`` с номером строки."""
    day, country, client_name, header_seen = None, "", "", False
    station, declared, station_seen = "", None, False
    wagons: list[WagonLine] = []
    issues: list[ReportIssue] = []
    seen: set[str] = set()

    for line_no, raw in enumerate(str(text or "").splitlines(), start=1):
        # split() без аргументов снимает и неразрывные пробелы WhatsApp.
        line = " ".join(raw.split())
        if not line:
            continue
        if match := _WAGON_RE.fullmatch(line):
            try:
                tons = Decimal(match["tons"].replace(",", "."))
            except InvalidOperation:  # pragma: no cover — регулярка пропускает только числа
                tons = Decimal("0")
            code = match["code"].strip()
            wagon = WagonLine(len(wagons) + 1, line_no, code, match_key(code), match["number"], tons)
            issues.extend(_wagon_issues(wagon, seen))
            seen.add(wagon.number)
            wagons.append(wagon)
        elif match := _HEADER_RE.fullmatch(line):
            if header_seen:
                # Остальное — уже другой отчёт: пусть его пришлют отдельно.
                issues.append(ReportIssue(
                    "several_reports", "В сообщении больше одного отчёта — пришлите каждый отдельно", line_no))
                break
            header_seen = True
            day = _header_day(match)
            if day is None:
                issues.append(ReportIssue("bad_date", f"Неверная дата: «{_quote(line)}»", line_no))
            country, client_name = _split_country(match["rest"] or "")
            if not client_name:
                issues.append(ReportIssue("client_missing", "В шапке не указан клиент", line_no))
        elif match := _STATION_RE.fullmatch(line):
            if station_seen:
                issues.append(ReportIssue("station_repeated", "Строка станции указана дважды", line_no))
                continue
            station_seen = True
            station = match["station"].strip(_SEPARATORS)
            declared = int(match["count"]) if match["count"] else None
            if declared is None:
                issues.append(ReportIssue(
                    "wagon_count_missing", "В строке станции не указано число вагонов", line_no))
        else:
            issues.append(ReportIssue("unknown_line", f"Непонятная строка: «{_quote(line)}»", line_no))

    if not header_seen:
        issues.append(ReportIssue(
            "header_missing", "Нет шапки с датой и клиентом: «сб 19.09.26 Узбекистан ООО …»"))
    if not station_seen:
        issues.append(ReportIssue("station_missing", "Нет строки станции: «Ст. Раустан 12 вагон»"))
    if not wagons:
        issues.append(ReportIssue("no_wagons", "Нет строк вагонов: «Д1с-28087658-68 тн»"))
    elif declared is not None and declared != len(wagons):
        issues.append(ReportIssue(
            "wagon_count_mismatch", f"Заявлено вагонов: {declared}, а строк вагонов: {len(wagons)}"))
    return RailReport(
        day=day,
        country=country,
        client_name=client_name,
        station=station,
        declared_wagons=declared,
        wagons=tuple(wagons),
        issues=tuple(issues),
    )


def format_rail_report(
    *, day: date, country: str, client_name: str, station: str, wagons: Iterable[tuple[str, str, Decimal]],
) -> str:
    """Отчёт в формате владельца — обратная сторона :func:`parse_rail_report`.

    ``wagons`` — (код товара, номер вагона, тонны). «Скопировать отчёт» в
    истории грузчика: текст разбирается обратно в те же вагоны. Страна не из
    списка шапки опускается — иначе разбор принял бы её за клиента.
    """
    wagons = list(wagons)
    country = country if country in REPORT_COUNTRIES else ""
    header = " ".join(part for part in (_WEEKDAYS[day.weekday()], f"{day:%d.%m.%y}", country, client_name) if part)
    station_line = " ".join(part for part in ("Ст.", station, f"{len(wagons)} вагон") if part)
    return "\n".join([
        header,
        station_line,
        *(f"{code}-{number}-{format_tons(tons)} тн" for code, number, tons in wagons),
    ])
