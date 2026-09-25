"""Итог и разбивка AI 24/7 по цветам: доли, поправки и архивные дни."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras import analytics
from apps.cameras.models import AlwaysOnCountArchive, AlwaysOnDailyAnalytics

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ai247_camera")]


def _count_today(colors):
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=timezone.localdate(),
        model_total=sum(colors.values()), model_per_color=colors)


def test_archived_days_stay_out_of_the_current_total():
    """Дни, ушедшие в архив, не возвращаются в «за всё время»."""
    yesterday = timezone.localdate() - timedelta(days=1)
    archive = AlwaysOnCountArchive.objects.create(
        camera="cam3", period_start=yesterday, period_end=yesterday,
        model_total=500, total=500, days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=500,
        model_per_color={"red": 500}, archived_at=timezone.now(), archive=archive)
    _count_today({"red": 80})

    assert analytics.today_payload()["all_time_total"] == 80


def test_day_payload_carries_its_own_colour_breakdown():
    """Клик по столбику показывает цвета того дня, а не за всё время."""
    yesterday = timezone.localdate() - timedelta(days=1)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday, model_total=100,
        model_per_color={"red": 100})
    _count_today({"blue": 30})

    history = {
        row["day"]: row for row in analytics.today_payload()["cameras"][0]["history"]
    }
    today_row = history[timezone.localdate().isoformat()]
    past_row = history[yesterday.isoformat()]

    assert [c["color"] for c in today_row["colors"]] == ["blue"]
    assert today_row["colors"][0]["percent"] == 100.0
    assert [c["color"] for c in past_row["colors"]] == ["red"]


@pytest.mark.parametrize("counts", [
    # Реальные цифры с экрана: 5931 + 1744 + 530 = 8205.
    {"red": 5931, "blue": 1744, "green": 530},
    {"red": 1, "blue": 1, "green": 1},        # 33.3 × 3 = 99.9
    {"red": 2, "blue": 1},                    # 66.7 + 33.3
    {"red": 1000, "blue": 1},                 # почти всё в один цвет
    {"red": 7, "blue": 7, "green": 7, "white": 7},
])
def test_percentages_sum_to_a_hundred_for_awkward_splits(counts):
    """Доли не должны давать 100.1% из-за поокругления каждой по отдельности."""
    _count_today(counts)

    colors = analytics.today_payload()["cameras"][0]["colors"]

    # Сравниваем в десятых: 33.3 + 33.3 + 33.4 в float даёт 99.99999999999999.
    assert sum(round(item["percent"] * 10) for item in colors) == 1000
    assert sum(item["total"] for item in colors) == sum(counts.values())


def test_colours_and_total_agree_after_a_manual_correction():
    """Ручная поправка не должна разводить сумму цветов и итог по смыслу."""
    _count_today({"red": 60, "blue": 40})
    AlwaysOnDailyAnalytics.objects.filter(camera="cam3").update(adjustment=-1)

    camera = analytics.today_payload()["cameras"][0]

    # Итог учитывает поправку, цвета описывают распознанное моделью —
    # разные величины, поэтому их разность объяснима adjustment'ом.
    assert camera["total"] == 99
    assert sum(c["total"] for c in camera["colors"]) == 100
    assert camera["adjustment"] == -1
