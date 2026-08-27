"""Обнуление 24/7-счётчика переносит накопленное в архив, а не теряет его."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.cameras import analytics
from apps.cameras.models import (
    AlwaysOnCountArchive,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
)

pytestmark = pytest.mark.django_db


def _enable(camera="cam3"):
    MonoblockCameraSettings.objects.update_or_create(
        singleton=True, defaults={"always_on_camera_sources": [camera]},
    )


def _snapshot(total, *, camera="cam3", colors=None):
    return {"processors": [{
        "cam": camera, "total": total, "mode": "always_on", "running": True,
        "per_color": colors or {},
    }]}


def test_archive_moves_the_total_and_resets_the_counter(boss):
    _enable()
    analytics.record_snapshot(_snapshot(100, colors={"Red_50": 70, "Blue_50": 30}))
    assert analytics.today_payload()["all_time_total"] == 100

    result = analytics.archive_camera("cam3", "конец месяца", boss)

    assert result["total"] == 100
    assert result["days"] == 1
    # Счётчик обнулён.
    payload = analytics.today_payload()
    assert payload["all_time_total"] == 0
    assert payload["total"] == 0
    assert payload["colors"] == []
    # Данные не потеряны.
    archive = AlwaysOnCountArchive.objects.get()
    assert archive.total == 100
    assert archive.model_per_color == {"red": 70, "blue": 30}
    assert archive.model_per_brand == {"unclassified": 100}
    assert result["brands"] == [
        {"brand": "unclassified", "total": 100, "percent": 100.0}
    ]
    assert archive.archived_by == boss


def test_archive_keeps_raw_cursor_baseline_and_counts_only_new_bags(boss):
    """CRM archive does not reset camera-PC; only its real delta is new."""
    _enable()
    analytics.record_snapshot(_snapshot(100))
    analytics.archive_camera("cam3", "", boss)
    assert AlwaysOnCounterCursor.objects.filter(camera="cam3").exists()

    # Воркер продолжает считать со своего числа — оно уже в архиве.
    analytics.record_snapshot(_snapshot(140))

    assert analytics.today_payload()["all_time_total"] == 40


def test_archived_days_stay_out_of_the_current_total(boss):
    """Старый день после архивации не возвращается в «за всё время»."""
    _enable()
    yesterday = timezone.localdate() - timedelta(days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=500,
        model_per_color={"red": 500})
    analytics.record_snapshot(_snapshot(80, colors={"Red_50": 80}))
    assert analytics.today_payload()["all_time_total"] == 580

    analytics.archive_camera("cam3", "", boss)

    assert analytics.today_payload()["all_time_total"] == 0
    assert AlwaysOnCountArchive.objects.get().total == 580


def test_archive_keeps_a_per_day_breakdown(boss):
    """Архив раскрывается по дням — включая день, когда его закрыли."""
    _enable()
    yesterday = timezone.localdate() - timedelta(days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=500,
        model_per_color={"red": 300, "blue": 200},
        model_per_brand={"korol": 300, "dikhan_baba": 180, "unknown": 20},
    )
    analytics.record_snapshot(_snapshot(80, colors={"Red_50": 80}))

    result = analytics.archive_camera("cam3", "", boss)

    by_day = {row["day"]: row for row in result["day_rows"]}
    assert set(by_day) == {yesterday.isoformat(), timezone.localdate().isoformat()}
    assert by_day[yesterday.isoformat()]["total"] == 500
    assert by_day[timezone.localdate().isoformat()]["total"] == 80
    # Сумма по дням сходится с итогом архива.
    assert sum(row["total"] for row in result["day_rows"]) == result["total"]
    # У каждого дня своя разбивка по цветам.
    assert [c["color"] for c in by_day[yesterday.isoformat()]["colors"]] == [
        "red",
        "blue",
    ]
    assert [b["brand"] for b in by_day[yesterday.isoformat()]["brands"]] == [
        "korol",
        "dikhan_baba",
        "unknown",
    ]
    assert by_day[timezone.localdate().isoformat()]["brands"] == [
        {"brand": "unclassified", "total": 80, "percent": 100.0}
    ]
    assert sum(item["total"] for item in result["brands"]) == result["model_total"]


def test_second_archive_does_not_borrow_days_from_the_first(boss):
    """Дни закрепляются за своим закрытием, а не за меткой времени."""
    _enable()
    analytics.record_snapshot(_snapshot(100))
    first = analytics.archive_camera("cam3", "первый", boss)
    analytics.record_snapshot(_snapshot(140))
    second = analytics.archive_camera("cam3", "второй", boss)

    assert sum(row["total"] for row in first["day_rows"]) == 100
    assert sum(row["total"] for row in second["day_rows"]) == 40


def test_deleting_an_archive_returns_its_bags_to_the_counter(boss):
    """Удаление архива — отмена переноса: мешки возвращаются, а не пропадают."""
    _enable()
    yesterday = timezone.localdate() - timedelta(days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=500,
        model_per_color={"red": 500},
        model_per_brand={"korol": 480, "unknown": 20},
    )
    analytics.record_snapshot(_snapshot(80, colors={"Red_50": 80}))
    archive = analytics.archive_camera("cam3", "ошибочное закрытие", boss)
    assert analytics.today_payload()["all_time_total"] == 0

    analytics.delete_archive(archive["id"], boss)

    payload = analytics.today_payload()
    assert payload["all_time_total"] == 580
    assert payload["model_per_brand"] == {
        "korol": 480,
        "unknown": 20,
        "unclassified": 80,
    }
    # День закрытия тоже вернулся — его вклад лежал снимком.
    assert payload["total"] == 80
    assert not AlwaysOnCountArchive.objects.filter(pk=archive["id"]).exists()


def test_deleting_one_archive_does_not_touch_another(boss):
    _enable()
    analytics.record_snapshot(_snapshot(100))
    first = analytics.archive_camera("cam3", "первый", boss)
    analytics.record_snapshot(_snapshot(140))
    second = analytics.archive_camera("cam3", "второй", boss)

    analytics.delete_archive(second["id"], boss)

    assert AlwaysOnCountArchive.objects.filter(pk=first["id"]).exists()
    assert analytics.today_payload()["all_time_total"] == 40


def test_deleting_a_missing_archive_is_rejected(boss):
    with pytest.raises(ValidationError) as exc:
        analytics.delete_archive(99999, boss)
    assert exc.value.detail["code"] == "archive_not_found"


def test_deleting_an_archive_requires_ai_247_manage(
    user_with_perms, auth_client, boss,
):
    """Кнопка удаления доступна тем же, кто может архивировать."""
    _enable()
    analytics.record_snapshot(_snapshot(100))
    archive = analytics.archive_camera("cam3", "", boss)
    loader = user_with_perms("archive-loader", codes=["shipping.load"])

    denied = auth_client(loader).delete(
        f"/api/cameras/always-on-analytics/archives/{archive['id']}/")
    assert denied.status_code == 403
    assert AlwaysOnCountArchive.objects.filter(pk=archive["id"]).exists()

    manager = user_with_perms("archive-manager", codes=["ai_247.manage"])
    allowed = auth_client(manager).delete(
        f"/api/cameras/always-on-analytics/archives/{archive['id']}/")
    assert allowed.status_code == 200
    assert not AlwaysOnCountArchive.objects.filter(pk=archive["id"]).exists()


def test_archive_rejects_an_empty_counter(boss):
    _enable()
    with pytest.raises(ValidationError) as exc:
        analytics.archive_camera("cam3", "", boss)
    assert exc.value.detail["code"] == "nothing_to_archive"


def test_archive_is_append_only(boss):
    """Второе закрытие добавляет строку, а не переписывает первую."""
    _enable()
    analytics.record_snapshot(_snapshot(100))
    analytics.archive_camera("cam3", "первый", boss)
    analytics.record_snapshot(_snapshot(140))
    analytics.archive_camera("cam3", "второй", boss)

    totals = sorted(AlwaysOnCountArchive.objects.values_list("total", flat=True))
    assert totals == [40, 100]


def test_day_payload_carries_its_own_colour_breakdown():
    """Клик по столбику показывает цвета того дня, а не за всё время."""
    _enable()
    yesterday = timezone.localdate() - timedelta(days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=100,
        model_per_color={"red": 100})
    analytics.record_snapshot(_snapshot(30, colors={"Blue_50": 30}))

    history = {row["day"]: row for row in analytics.today_payload()["history"]}
    today_row = history[timezone.localdate().isoformat()]
    past_row = history[yesterday.isoformat()]

    assert [c["color"] for c in today_row["colors"]] == ["blue"]
    assert today_row["colors"][0]["percent"] == 100.0
    assert [c["color"] for c in past_row["colors"]] == ["red"]


def test_colour_percentages_always_sum_to_a_hundred():
    """Доли не должны давать 100.1% из-за поокругления каждой по отдельности."""
    _enable()
    # Реальные цифры с экрана: 5931 + 1744 + 530 = 8205.
    analytics.record_snapshot(_snapshot(
        8205, colors={"Red_50": 5931, "Blue_50": 1744, "Green_50": 530}))

    colors = analytics.today_payload()["colors"]

    assert sum(item["total"] for item in colors) == 8205
    # Сравниваем в десятых: 33.3 + 33.3 + 33.4 в float даёт 99.99999999999999.
    assert sum(round(item["percent"] * 10) for item in colors) == 1000


@pytest.mark.parametrize("counts", [
    {"Red_50": 1, "Blue_50": 1, "Green_50": 1},        # 33.3 × 3 = 99.9
    {"Red_50": 2, "Blue_50": 1},                        # 66.7 + 33.3
    {"Red_50": 1000, "Blue_50": 1},                     # почти всё в один цвет
    {"Red_50": 7, "Blue_50": 7, "Green_50": 7, "White_50": 7},
])
def test_percentages_sum_to_a_hundred_for_awkward_splits(counts):
    _enable()
    analytics.record_snapshot(_snapshot(sum(counts.values()), colors=counts))

    colors = analytics.today_payload()["colors"]

    # Сравниваем в десятых: 33.3 + 33.3 + 33.4 в float даёт 99.99999999999999.
    assert sum(round(item["percent"] * 10) for item in colors) == 1000
    assert sum(item["total"] for item in colors) == sum(counts.values())


def test_colours_and_total_agree_after_a_manual_correction(boss):
    """Ручная поправка не должна разводить сумму цветов и итог по смыслу."""
    _enable()
    analytics.record_snapshot(_snapshot(100, colors={"Red_50": 60, "Blue_50": 40}))
    analytics.subtract_today("cam3", 1, "просыпали мешок", boss, "red")

    payload = analytics.today_payload()

    # Итог учитывает поправку, цвета описывают распознанное моделью —
    # разные величины, поэтому их разность объяснима adjustment'ом.
    assert payload["total"] == 99
    assert sum(c["total"] for c in payload["colors"]) == 100
    assert payload["adjustment"] == -1
