"""Ручная правка аналитики камер моноблока: только суперпользователь и только аналитика.

Счёт камеры (model_*, total_bags сессии, связи событий) не меняется: правка
хранится разницей по цветам поверх него, поэтому новые мешки идут сверху.
"""
from datetime import datetime, time, timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from apps.cameras import analytics, color_resolution, shipping_segments
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    ShippingDailyAnalytics,
    ShippingLoadingSegment,
    ShippingLoadingSession,
    ShippingSessionSettings,
)
from apps.cameras.tests.shipping_fakes import add_events
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db
SHIPPING = "/api/cameras/shipping-continuous-analytics/"
AI247 = "/api/cameras/always-on-analytics/"
SESSIONS = "/api/cameras/shipping-sessions/"


@pytest.fixture(autouse=True)
def contours(db):
    """cam2 — отгрузка, cam3 — AI 24/7, cam5 закреплена за отгрузкой, но выключена."""
    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"camera_sources": ["cam2"], "always_on_camera_sources": ["cam3"]},
    )
    for camera, scope in (
        ("cam2", ANALYTICS_SCOPE_SHIPPING),
        ("cam3", ANALYTICS_SCOPE_AI247),
        ("cam5", ANALYTICS_SCOPE_SHIPPING),
    ):
        ContinuousCameraRole.objects.update_or_create(
            camera=camera, defaults={"analytics_scope": scope}
        )


def yesterday():
    return timezone.localdate() - timedelta(days=1)


def put_day(client, url, colors, *, camera="cam2", day=None, query=""):
    body = {"camera": camera, "day": (day or yesterday()).isoformat(), "colors": colors}
    return client.put(url + query, body, format="json")


def day_point(payload, day):
    [camera] = payload["cameras"]
    return next(row for row in camera["history"] if row["day"] == day.isoformat())


def totals(colors):
    return {item["color"]: item["total"] for item in colors}


# ---------------------------------------------------------------------------
# Day analytics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url, camera", [(SHIPPING, "cam2"), (AI247, "cam3")])
def test_only_superuser_edits_day_analytics(
    auth_client, admin_user, operator, boss, client_user, url, camera
):
    # Права Моноблока и даже управление правами не дают править аналитику.
    for user in (operator, boss, client_user):
        assert put_day(auth_client(user), url, {"blue": 5}, camera=camera).status_code == 403
    assert not EventLog.objects.filter(event_type="camera_analytics_edited").exists()
    assert put_day(auth_client(admin_user), url, {"blue": 5}, camera=camera).status_code == 200


def test_colour_deltas_clamp_and_are_measured_against_what_was_shown():
    stored = {"blue": -5, "red": 3, "white": True, "green": 1.5, "x": "4", "y": 0}
    assert analytics.normalized_adjustments(stored) == {"blue": -5, "red": 3}
    assert analytics.adjusted_colors({"blue": 2, "white": 1}, stored) == {"white": 1, "red": 3}
    # Синих показано 0 (2 − 5 обрезано), стало 4: итог +4, поправка — от камеры.
    assert analytics.apply_color_targets({"blue": 2}, {"blue": -5}, {"blue": 4}) == ({"blue": 2}, 4)


def test_day_edit_moves_colours_and_total_on_top_of_the_camera(auth_client, admin_user):
    day = yesterday()
    # Старая поправка только итога (как cam2 за 10.09) должна сохраниться.
    ShippingDailyAnalytics.objects.create(
        camera="cam2", day=day, model_total=10,
        model_per_color={"blue": 6, "white": 4}, adjustment=-2,
    )
    ShippingDailyAnalytics.objects.create(
        camera="cam2", day=day - timedelta(days=5), model_total=7, model_per_color={"blue": 7}
    )
    api = auth_client(admin_user)
    query = f"?camera=cam2&date_from={day}&date_to={timezone.localdate()}"

    response = put_day(api, SHIPPING, {"blue": 3, "red": 5}, query=query)

    assert response.status_code == 200
    # −3 синих и +5 красных: итог сдвинулся ровно на видимую разницу.
    row = ShippingDailyAnalytics.objects.get(camera="cam2", day=day)
    assert (row.model_total, row.model_per_color) == (10, {"blue": 6, "white": 4})
    assert (row.adjustment, row.adjustment_per_color, row.total) == (0, {"blue": -3, "red": 5}, 10)
    point = day_point(response.data, day)
    assert totals(point["colors"]) == {"red": 5, "white": 4, "blue": 3}
    assert point["model_per_color"] == {"blue": 6, "white": 4}
    assert point["adjustment_per_color"] == {"blue": -3, "red": 5}
    assert point["total"] == 10
    [camera] = response.data["cameras"]
    assert (camera["period_total"], camera["all_time_total"]) == (10, 17)
    assert totals(camera["colors"]) == {"red": 5, "white": 4, "blue": 3}
    assert response.data == api.get(SHIPPING + query).data

    # «Как у камеры» возвращает цвета камеры и старую поправку итога.
    put_day(api, SHIPPING, {"blue": 6, "red": 0, "white": 4})
    row.refresh_from_db()
    assert (row.adjustment, row.adjustment_per_color, row.total) == (-2, {}, 8)


def test_bag_counted_after_an_edit_adds_on_top(auth_client, admin_user):
    today = timezone.localdate()
    ShippingDailyAnalytics.objects.create(
        camera="cam2", day=today, model_total=3, model_per_color={"blue": 3}
    )
    assert put_day(auth_client(admin_user), SHIPPING, {"blue": 10}, day=today).status_code == 200

    with transaction.atomic():
        analytics.record_counted_bag(
            camera="cam2", color="blue", brand=None,
            observed_at=timezone.now(), analytics_scope=ANALYTICS_SCOPE_SHIPPING,
        )

    [camera] = analytics.today_payload(ANALYTICS_SCOPE_SHIPPING)["cameras"]
    assert (camera["model_total"], camera["total"], camera["all_time_total"]) == (4, 11, 11)
    assert camera["model_per_color"] == {"blue": 4}
    assert camera["adjustment_per_color"] == {"blue": 7}
    assert totals(camera["colors"]) == {"blue": 11}


def ai247_day_with_unknown_bags(day, *, resolved_color="", method=""):
    """cam3: 3 синих и 2 мешка, которые камера оставила «unknown»."""
    noon = timezone.make_aware(datetime.combine(day, time(12)))
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=day, model_total=5, model_per_color={"blue": 3, "unknown": 2}
    )
    AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(
            camera="cam3", upstream_event_id=i + 1, occurred_at=noon + timedelta(seconds=i),
            source="sub", mode="always_on", analytics_scope=ANALYTICS_SCOPE_AI247,
            applied_to_analytics=True, color="unknown",
            resolved_color=resolved_color, color_resolution=method,
        )
        for i in range(2)
    ])


def resolve_unknown_bags(color, method):
    AlwaysOnImportedEvent.objects.filter(camera="cam3").update(
        resolved_color=color, color_resolution=method
    )


def test_ai247_edit_uses_resolved_colours_and_never_saves_the_overlay(auth_client, admin_user):
    day = yesterday()
    # Два мешка камера оставила «unknown», CRM нашла им синий цвет по соседям.
    ai247_day_with_unknown_bags(day, resolved_color="blue", method="neighbors")

    response = put_day(
        auth_client(admin_user), AI247, {"blue": 4}, camera="cam3",
        query=f"?camera=cam3&date_from={day}&date_to={day}",
    )

    assert response.status_code == 200
    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3", day=day)
    assert row.model_per_color == {"blue": 3, "unknown": 2}
    assert (row.model_total, row.adjustment, row.adjustment_per_color) == (5, -1, {"blue": -1})
    point = day_point(response.data, day)
    assert point["model_per_color"] == {"blue": 5}
    assert point["colors"] == [{"color": "blue", "total": 4, "percent": 100.0}]
    assert point["total"] == 4


@pytest.mark.parametrize("method", ["neighbors", "manual"])
@pytest.mark.parametrize("resolved_first", [False, True], ids=["resolved-after", "resolved-before"])
def test_ai247_bags_moved_by_hand_are_not_moved_again_by_resolution(
    auth_client, admin_user, method, resolved_first
):
    day = yesterday()
    ai247_day_with_unknown_bags(day)
    if resolved_first:
        resolve_unknown_bags("blue", method)
    api = auth_client(admin_user)
    query = f"?camera=cam3&date_from={day}&date_to={day}"

    # Главный случай редактора: мешки «Не определён» переносятся в свой цвет.
    assert put_day(api, AI247, {"blue": 5, "unknown": 0}, camera="cam3").status_code == 200
    # Позже CRM (соседи после импорта / «Указать цвет») находит им тот же цвет.
    if not resolved_first:
        resolve_unknown_bags("blue", method)

    data = api.get(AI247 + query).data
    [camera] = data["cameras"]
    point = day_point(data, day)
    assert totals(point["colors"]) == {"blue": 5}
    assert sum(totals(point["colors"]).values()) == point["total"] == 5
    assert totals(camera["colors"]) == {"blue": 5}
    assert camera["period_total"] == 5
    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3", day=day)
    assert (row.model_total, row.model_per_color) == (5, {"blue": 3, "unknown": 2})


def test_ai247_resolved_colour_lowered_below_its_inferred_bags(auth_client, admin_user):
    day = yesterday()
    # Камера: 5 синих, из них 2 найдены по соседям.
    ai247_day_with_unknown_bags(day, resolved_color="blue", method="neighbors")
    query = f"?camera=cam3&date_from={day}&date_to={day}"

    response = put_day(auth_client(admin_user), AI247, {"blue": 1, "red": 4}, camera="cam3", query=query)

    assert response.status_code == 200
    [camera] = response.data["cameras"]
    point = day_point(response.data, day)
    assert point["model_per_color"] == {"blue": 5}
    assert totals(point["colors"]) == {"red": 4, "blue": 1}
    assert point["total"] == camera["period_total"] == 5
    # Метка «по соседям» не больше показанных синих мешков.
    assert camera["colors"] == [
        {"color": "red", "total": 4, "percent": 80.0},
        {"color": "blue", "total": 1, "percent": 20.0, "inferred": {"neighbors": 1}},
    ]

    response = put_day(auth_client(admin_user), AI247, {"blue": 0, "red": 5}, camera="cam3", query=query)

    [camera] = response.data["cameras"]
    assert camera["colors"] == [{"color": "red", "total": 5, "percent": 100.0}]


def test_inferred_marker_never_exceeds_the_shown_count():
    items = [{"color": "blue", "total": 2}, {"color": "red", "total": 0}]
    parts = [{"blue": {"neighbors": 2, "votes": 1}}, {"blue": {"manual": 1}, "red": {"votes": 3}}]

    color_resolution.with_inferred(items, "color", parts)

    # Ручное назначение занимает место первым, как при переносе мешков.
    assert items == [
        {"color": "blue", "total": 2, "inferred": {"manual": 1, "votes": 1}},
        {"color": "red", "total": 0},
    ]


def test_legacy_negative_total_adjustment_keeps_following_edits(auth_client, admin_user):
    day = yesterday()
    # Старое «уменьшить итог»: −50 к итогу, цвета не тронуты.
    ShippingDailyAnalytics.objects.create(
        camera="cam2", day=day, model_total=100, model_per_color={"blue": 100}, adjustment=-50
    )
    api = auth_client(admin_user)

    put_day(api, SHIPPING, {"blue": 0})
    row = ShippingDailyAnalytics.objects.get(camera="cam2", day=day)
    assert (row.adjustment, row.total) == (-100, 0)

    # «Обнулить и ввести заново»: итог снова растёт на видимую разницу.
    put_day(api, SHIPPING, {"blue": 30})
    row.refresh_from_db()
    assert (row.model_total, row.adjustment, row.total) == (100, -70, 30)
    assert row.adjustment_per_color == {"blue": -70}


def test_adjustment_below_the_camera_count_is_lifted_before_the_edit(auth_client, admin_user):
    day = yesterday()
    ShippingDailyAnalytics.objects.create(
        camera="cam2", day=day, model_total=10, model_per_color={"blue": 10}, adjustment=-20
    )

    put_day(auth_client(admin_user), SHIPPING, {"blue": 15})

    row = ShippingDailyAnalytics.objects.get(camera="cam2", day=day)
    assert (row.adjustment, row.total) == (-5, 5)
    # Мешок, посчитанный после правки, прибавляется к итогу.
    with transaction.atomic():
        analytics.record_counted_bag(
            camera="cam2", color="blue", brand=None,
            observed_at=timezone.make_aware(datetime.combine(day, time(12))),
            analytics_scope=ANALYTICS_SCOPE_SHIPPING,
        )
    row.refresh_from_db()
    assert row.total == 6


def test_archived_ai247_day_cannot_be_edited(auth_client, admin_user):
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=yesterday(), model_total=5,
        model_per_color={"red": 5}, archived_at=timezone.now(),
    )

    response = put_day(auth_client(admin_user), AI247, {"red": 3}, camera="cam3")

    assert response.status_code == 400
    assert response.data["code"] == "day_archived"
    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert (row.adjustment, row.adjustment_per_color) == (0, {})
    assert not EventLog.objects.filter(event_type="camera_analytics_edited").exists()


@pytest.mark.parametrize("body, query", [
    ({"day": "tomorrow"}, ""),
    ({"day": "9999-12-31"}, ""),
    ({"day": "0001-01-01"}, ""),
    ({"colors": {"purple": 3}}, ""),
    ({"colors": {"blue": True}}, ""),
    ({"colors": {"blue": 5.0}}, ""),
    ({"colors": {"blue": "5"}}, ""),
    ({"colors": {"blue": -1}}, ""),
    ({"colors": {"blue": 1_000_001}}, ""),
    ({"colors": {"Blue": 5}}, ""),
    ({"colors": {"_" * (i + 1): 1 for i in range(17)}}, ""),
    ({"colors": {}}, ""),
    ({"colors": [5]}, ""),
    ({"camera": "cam3"}, ""),
    ({"camera": "cam5"}, ""),
    ({"camera": "../cam2"}, ""),
    ({}, "?date_from=2026-09-10&date_to=2026-09-01"),
])
def test_invalid_day_edit_is_rejected_without_writing(auth_client, admin_user, body, query):
    payload = {"camera": "cam2", "day": yesterday().isoformat(), "colors": {"blue": 5}} | body
    if payload["day"] == "tomorrow":
        payload["day"] = (timezone.localdate() + timedelta(days=1)).isoformat()

    response = auth_client(admin_user).put(SHIPPING + query, payload, format="json")

    assert response.status_code == 400
    assert not ShippingDailyAnalytics.objects.exists()
    assert not EventLog.objects.filter(event_type="camera_analytics_edited").exists()


def test_day_edit_is_audited_and_can_fill_an_empty_day(auth_client, admin_user):
    day = yesterday()

    response = put_day(auth_client(admin_user), SHIPPING, {"white": 12, "blue": 0})

    assert response.status_code == 200
    row = ShippingDailyAnalytics.objects.get(camera="cam2", day=day)
    assert (row.model_total, row.adjustment, row.total) == (0, 12, 12)
    event = EventLog.objects.get(event_type="camera_analytics_edited")
    assert event.user == admin_user
    assert event.payload == {
        "scope": ANALYTICS_SCOPE_SHIPPING, "camera": "cam2", "day": day.isoformat(),
        "camera_colors": {}, "colors_before": {}, "colors_after": {"white": 12},
        "total_before": 0, "total_after": 12, "adjustment_before": 0, "adjustment_after": 12,
    }


# ---------------------------------------------------------------------------
# Wagon/truck sessions
# ---------------------------------------------------------------------------


@pytest.fixture
def start():
    start = timezone.now() - timedelta(hours=2)
    ShippingSessionSettings.objects.update_or_create(
        singleton=True, defaults={"activated_at": start, "idle_timeout_seconds": 300}
    )
    return start


def project(start, seconds, colors):
    add_events(start, seconds, colors=colors)
    shipping_segments.ingest_camera("cam2")


def listed(api, at):
    return api.get(SESSIONS, {"day": timezone.localtime(at).date().isoformat()}).data["results"]


def patch_session(api, session_id, colors):
    return api.patch(f"{SESSIONS}{session_id}/", {"colors": colors}, format="json")


def test_only_superuser_edits_a_session(auth_client, admin_user, operator, boss, client_user, start):
    project(start, [0], ["blue"])
    session = ShippingLoadingSession.objects.get()
    for user in (operator, boss, client_user):
        assert patch_session(auth_client(user), session.pk, {"blue": 3}).status_code == 403
    session.refresh_from_db()
    assert session.colors_adjustment == {}
    assert patch_session(auth_client(admin_user), session.pk, {"blue": 3}).status_code == 200


def test_session_edit_shows_corrected_colours_over_the_camera_count(auth_client, admin_user, start):
    project(start, [0, 1, 2, 3], ["blue", "blue", "white", None])
    session = ShippingLoadingSession.objects.get()
    api = auth_client(admin_user)
    [before] = listed(api, start)
    assert (before["total_bags"], before["camera_total_bags"], before["edited"]) == (4, 4, False)

    response = patch_session(api, session.pk, {"blue": 5, "unclassified": 0})

    assert response.status_code == 200
    data = response.data
    assert (data["total_bags"], data["camera_total_bags"], data["edited"]) == (6, 4, True)
    assert data["camera_colors"] == {"blue": 2, "white": 1, "unclassified": 1}
    assert data["colors"] == [
        {"color": "blue", "total": 5, "percent": 83.3},
        {"color": "white", "total": 1, "percent": 16.7},
    ]
    assert [part["total_bags"] for part in data["segments"]] == [4]
    assert listed(api, start) == [data]
    session.refresh_from_db()
    assert (session.total_bags, session.colors_adjustment) == (4, {"blue": 3, "unclassified": -1})
    event = EventLog.objects.get(event_type="shipping_session_edited")
    assert event.user == admin_user
    assert event.payload == {
        "session_id": session.pk, "camera": "cam2", "number": "", "camera_total": 4,
        "camera_colors": {"blue": 2, "white": 1, "unclassified": 1},
        "colors_before": {"blue": 2, "white": 1, "unclassified": 1},
        "colors_after": {"blue": 5, "white": 1},
        "total_before": 4, "total_after": 6,
    }

    # Мешки, посчитанные после правки, прибавляются сверху.
    project(start, [10, 11], ["blue", "white"])
    [row] = listed(api, start)
    assert (row["total_bags"], row["camera_total_bags"]) == (8, 6)
    assert totals(row["colors"]) == {"blue": 6, "white": 2}


def test_session_without_crossings_counts_its_total_as_unclassified(auth_client, admin_user):
    now = timezone.now()
    session = ShippingLoadingSession.objects.create(
        camera="cam2", status="closed", total_bags=7,
        started_at=now, last_counted_at=now, ended_at=now,
    )
    api = auth_client(admin_user)
    [row] = listed(api, now)
    assert row["camera_colors"] == {"unclassified": 7}
    assert row["colors"] == [{"color": "unclassified", "total": 7, "percent": 100.0}]

    response = patch_session(api, session.pk, {"white": 7, "unclassified": 0})

    assert response.data["colors"] == [{"color": "white", "total": 7, "percent": 100.0}]
    assert response.data["total_bags"] == 7


@pytest.mark.parametrize("colors", [{"purple": 1}, {"blue": True}, {"blue": 2.0}, {"blue": -1}, {}, None])
def test_invalid_session_edit_is_rejected(auth_client, admin_user, start, colors):
    project(start, [0], ["blue"])
    session = ShippingLoadingSession.objects.get()
    assert patch_session(auth_client(admin_user), session.pk, colors).status_code == 400
    session.refresh_from_db()
    assert session.colors_adjustment == {}
    assert not EventLog.objects.filter(event_type="shipping_session_edited").exists()


def test_merged_or_missing_session_is_not_found(auth_client, admin_user, start):
    project(start, [0, 400], ["blue", "white"])
    for part in ShippingLoadingSegment.objects.all():
        shipping_segments.apply_identity(part.pk, "123ABC13", "model", recognition_model="vehicle_number")
    merged = ShippingLoadingSession.objects.get(status="merged")
    api = auth_client(admin_user)
    assert patch_session(api, merged.pk, {"blue": 3}).status_code == 404
    assert patch_session(api, merged.pk + 1000, {"blue": 3}).status_code == 404


def test_merging_sessions_keeps_their_manual_corrections(auth_client, admin_user, start):
    project(start, [0, 1, 400, 401], ["blue", "blue", "white", "white"])
    first, second = ShippingLoadingSegment.objects.order_by("first_upstream_event_id")
    api = auth_client(admin_user)
    patch_session(api, first.session_id, {"blue": 5})
    patch_session(api, second.session_id, {"white": 1, "red": 4, "blue": 1})

    for part in (first, second):
        shipping_segments.apply_identity(part.pk, "123ABC13", "model", recognition_model="vehicle_number")

    owner = ShippingLoadingSession.objects.get(pk=first.session_id)
    merged = ShippingLoadingSession.objects.get(pk=second.session_id)
    assert (merged.status, merged.merged_into_id, merged.colors_adjustment) == ("merged", owner.pk, {})
    assert owner.colors_adjustment == {"blue": 4, "white": -1, "red": 4}
    [row] = listed(api, start)
    assert (row["id"], row["camera_total_bags"], row["total_bags"]) == (owner.pk, 4, 11)
    assert totals(row["colors"]) == {"blue": 6, "red": 4, "white": 1}
