"""Отчёт владельца (встреча 23.09) — общий пример для тестов разбора и проведения."""
from datetime import date

OWNER_DAY = date(2026, 9, 19)
OWNER_BAGS = 16320  # 12 вагонов × 68 т мешками по 50 кг
# Провести отчёт: создать и подтвердить заказ и отгрузить вагоны.
CONDUCT_CODES = ["orders.view", "orders.create", "orders.confirm", "loader.view", "loader.confirm", "loader.wagons"]

# 12 вагонов с верной контрольной цифрой; первый — из отчёта владельца.
OWNER_WAGONS = (
    "28087658", "28087666", "28087674", "28087682", "28087690", "28087708",
    "28087716", "28087724", "28087732", "28087740", "28087757", "28087765",
)

# Строки как в WhatsApp: кириллические «ООО» и «Д1с», два пробела после страны.
OWNER_REPORT = "\n".join(
    [
        "сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL",
        "Ст. Раустан 12 вагон",
        *(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS),
    ]
)


def report(*wagon_lines: str, header="сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL", station=None) -> str:
    """Отчёт с заданными строками вагонов; станция по умолчанию — с их числом."""
    station = f"Ст. Раустан {len(wagon_lines)} вагон" if station is None else station
    return "\n".join(line for line in (header, station, *wagon_lines) if line is not None)
