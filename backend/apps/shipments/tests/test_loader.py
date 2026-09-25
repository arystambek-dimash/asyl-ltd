from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment, WaybillSettings
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["loader.view", "loader.confirm", "loader.trucks", "loader.wagons"])


def _bags(product):
    return sum(StockItem.objects.filter(product=product).values_list("bags", flat=True))


def test_queue_shows_orders_waiting_for_shipment(auth_client, loader, product, make_order):
    waiting = make_order(product)
    on_post = make_order(product, status="arrived", transport_type="train")
    make_order(product, status="pending")
    shipped = make_order(product, status="shipped")

    rows = auth_client(loader).get("/api/loader/queue/").data

    assert [row["id"] for row in rows] == [waiting.pk, on_post.pk]
    assert rows[0]["client_name"] == "ИП Мурат"
    assert rows[0]["bags"] == 2
    assert rows[0]["total_kg"] == "100.00"
    assert rows[0]["total_amount"] == "20000.00"
    # Оплата видна сразу: клиент мог заплатить заранее, грузчик это знает.
    assert rows[0]["payment_status"] == "unpaid"
    assert rows[0]["remaining_amount"] == "20000.00"
    assert shipped.pk not in [row["id"] for row in rows]


def test_queue_payment_status_follows_money_not_stored_field(auth_client, boss, loader, product, make_order):
    """Сохранённый payment_status может отстать (ORD-33): грузчик видит статус по факту денег."""
    from apps.orders.services import record_staff_payment

    order = make_order(product, quantity=2)
    record_staff_payment(order, "20000.00", boss, method="cash")
    Order.objects.filter(pk=order.pk).update(payment_status="unpaid")

    [row] = auth_client(loader).get("/api/loader/queue/").data

    assert (row["payment_status"], row["remaining_amount"]) == ("settled", "0.00")


def test_queue_filters_by_planned_day_and_search(auth_client, loader, product, make_order):
    tomorrow = timezone.localdate() + timedelta(days=1)
    today_order = make_order(product)
    tomorrow_order = make_order(product, arrival_date=tomorrow, truck_number="612BEX13")
    api = auth_client(loader)

    assert [row["id"] for row in api.get(f"/api/loader/queue/?day={tomorrow}").data] == [tomorrow_order.pk]
    assert [row["id"] for row in api.get("/api/loader/queue/?search=612").data] == [tomorrow_order.pk]
    # Плановый день считает сервер: дата приезда, без неё — день создания.
    planned = {row["id"]: row["planned_on"] for row in api.get("/api/loader/queue/").data}
    assert planned == {today_order.pk: str(timezone.localdate()), tomorrow_order.pk: str(tomorrow)}


def test_queue_separates_overdue_from_today(auth_client, loader, product, make_order):
    today = timezone.localdate()
    fresh = make_order(product, arrival_date=today)
    late = make_order(product, arrival_date=today - timedelta(days=30))
    api = auth_client(loader)

    assert [row["id"] for row in api.get("/api/loader/queue/?overdue=1").data] == [late.pk]
    assert [row["id"] for row in api.get(f"/api/loader/queue/?day={today}").data] == [fresh.pk]
    assert {row["id"] for row in api.get("/api/loader/queue/").data} == {fresh.pk, late.pk}


def test_one_button_ships_ordered_quantity_and_prints_waybill(auth_client, loader, product, make_order):
    order = make_order(product, quantity=3)
    api = auth_client(loader)

    response = api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 bjn 13"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.payment_status == "unpaid"
    # Номер хранится слитно: «403 bjn 13» с экрана грузчика и «403BJN13» из формы — одна машина.
    assert order.truck_number == "403BJN13"
    assert Shipment.objects.get(order=order).bags_loaded == 3
    assert _bags(product) == 97
    assert EventLog.objects.filter(order=order, event_type="shipment", message__contains=f"№{order.pk}").exists()

    history = api.get("/api/loader/history/").data
    assert [row["id"] for row in history] == [order.pk]

    waybill = api.get(f"/api/loader/orders/{order.pk}/waybill/")
    assert waybill.status_code == 200
    assert waybill["Content-Type"] == "application/pdf"
    assert waybill.content.startswith(b"%PDF")


def test_dispatch_is_single_and_only_for_waiting_orders(auth_client, loader, product, make_order):
    pending = make_order(product, status="pending")
    order = make_order(product)
    api = auth_client(loader)

    assert api.post(f"/api/loader/orders/{pending.pk}/dispatch/", {}, format="json").status_code == 400
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 200
    # Повторное нажатие не списывает склад второй раз.
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 400
    assert _bags(product) == 98
    assert api.get(f"/api/loader/orders/{pending.pk}/waybill/").status_code == 400


def test_history_filters_by_shipment_date(auth_client, loader, product, make_order):
    order = make_order(product)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")
    yesterday = timezone.localdate() - timedelta(days=1)

    assert api.get(f"/api/loader/history/?date_from={yesterday}&date_to={yesterday}").data == []


def test_confirm_requires_loader_confirm_permission(auth_client, user_with_perms, product, make_order):
    viewer = user_with_perms("loader-viewer", codes=["loader.view"])
    order = make_order(product)
    api = auth_client(viewer)

    assert api.get("/api/loader/queue/").status_code == 200
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 403
    assert auth_client(user_with_perms("stranger", codes=["orders.view"])).get("/api/loader/queue/").status_code == 403


def test_waybill_settings_are_read_by_loader_and_changed_by_admin(auth_client, loader, user_with_perms):
    admin = user_with_perms("rights-admin", codes=["sys_permissions.manage"])
    payload = {"point_name": "мельница Аксу", "signers": [{"role": "Кладовщик", "name": "Тажи А"}]}

    assert auth_client(loader).get("/api/loader/waybill-settings/").data["signers"][0]["name"] == "Егамбердиева Д"
    assert auth_client(loader).put("/api/loader/waybill-settings/", payload, format="json").status_code == 403

    response = auth_client(admin).put("/api/loader/waybill-settings/", payload, format="json")

    assert response.status_code == 200
    assert WaybillSettings.load().signers == [{"role": "Кладовщик", "name": "Тажи А"}]


def test_dispatch_closes_open_ai_counting_with_its_bags_then_ships(
    auth_client, loader, product, monkeypatch, make_order,
):
    """Кнопок погрузки в Моноблоке нет: открытый AI-подсчёт закрывает сама отгрузка."""
    from apps.cameras import counting
    from apps.cameras.models import AiCountingSession
    from apps.shipments.services import finish_ai_counting

    order = make_order(product, status="loading", quantity=5)
    Shipment.objects.create(order=order)
    session = AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)
    calls = []

    def fake_stop(camera, stopped_order, user, *, complete_order, expected_session_id):
        calls.append((camera, complete_order, expected_session_id))
        finish_ai_counting(stopped_order, 4, user)
        AiCountingSession.objects.filter(pk=session.pk).update(status=AiCountingSession.CLOSED)
        return {}

    monkeypatch.setattr(counting, "stop", fake_stop)
    response = auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 200, response.data
    assert calls == [("cam2", True, session.pk)]
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.shipment.bags_loaded == 4


def test_dispatch_keeps_order_when_camera_pc_is_unreachable(auth_client, loader, product, monkeypatch, make_order):
    from apps.cameras import ai, counting
    from apps.cameras.models import AiCountingSession

    order = make_order(product, status="loading")
    AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)

    def unreachable(*args, **kwargs):
        raise ai.AiUnavailable("down")

    monkeypatch.setattr(counting, "stop", unreachable)
    response = auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"
    order.refresh_from_db()
    assert order.status == "loading"
    assert _bags(product) == 100


def test_loader_undoes_own_fresh_dispatch(auth_client, loader, product, make_order):
    order = make_order(product, quantity=3)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 BJN 13"}, format="json")
    before = _bags(product)

    history = api.get("/api/loader/history/").data
    assert [row["can_rollback"] for row in history] == [True]

    response = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")

    assert response.status_code == 200, response.data
    # Экран возвращает строку ответа в очередь по плановому дню.
    assert response.data["planned_on"] == str(timezone.localdate())
    order.refresh_from_db()
    assert order.status == "confirmed"
    # Мешки вернулись на склад, а заказ снова ждёт отгрузки.
    assert _bags(product) == before + 3
    assert [row["id"] for row in api.get("/api/loader/queue/").data] == [order.pk]
    assert EventLog.objects.filter(order=order, event_type="shipment_rollback").exists()


def test_loader_cannot_undo_an_old_or_foreign_dispatch(auth_client, loader, user_with_perms, product, make_order):
    order = make_order(product)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 BJN 13"}, format="json")

    # Чужая отгрузка: другой грузчик её не отменяет.
    other = user_with_perms("loader-2", codes=["loader.view", "loader.confirm", "loader.trucks"])
    foreign = auth_client(other).post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")
    assert foreign.status_code == 400
    assert foreign.data["code"] == "rollback_not_allowed"
    assert auth_client(other).get("/api/loader/history/").data[0]["can_rollback"] is False

    # Своя, но старше часа: дальше откат оформляет старший в «Заказах».
    shipment = Shipment.objects.get(order=order)
    shipment.shipped_at = timezone.now() - timedelta(hours=2)
    shipment.save(update_fields=["shipped_at"])
    late = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")
    assert late.status_code == 400
    assert "часа" in late.data["detail"]
    order.refresh_from_db()
    assert order.status == "shipped"


def test_dispatch_matches_the_number_in_any_spelling(auth_client, loader, manager, product, make_order):
    """Номер из формы («403BJN13») и с экрана грузчика («403 bjn 13») — одна машина.

    Раньше разница записи считалась сменой номера: после въезда это давало
    «номер нельзя изменить», а у номера клиента — «задан другим пользователем».
    """
    order = make_order(product, status="arrived", truck_number="403BJN13", truck_number_set_by=manager)

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 bjn 13"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.status, order.truck_number, order.truck_number_set_by) == ("shipped", "403BJN13", manager)


def test_dispatch_fills_the_trailer_and_notifies_client_once(auth_client, loader, product, make_order):
    """Одно уведомление об отгрузке — без номеров: машина уже на территории,
    а её номер клиенту не показывается (решение владельца, как в портале)."""
    order = make_order(product, status="arrived")

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/",
        {"truck_number": "07 kg 695 adt", "trailer_number": "07 kg 837 pb"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")
    assert list(Notification.objects.filter(client=order.client).values_list("text", flat=True)) == [
        f"Заказ №{order.pk} отгружен"
    ]


def test_dispatch_clears_a_trailer_sent_empty_and_keeps_one_not_sent(auth_client, loader, manager, product, make_order):
    """Пустой прицеп на кнопке — «стереть» (грузчик убрал ошибочный), как в форме
    заказа и «Фурах»; непереданный — «не менять»: экран шлёт только исправленное."""
    kept = make_order(product, truck_number="403BJN13", trailer_number="07KG837PB", truck_number_set_by=manager)
    cleared = make_order(product, truck_number="612BEX13", trailer_number="07KG837PB", truck_number_set_by=manager)
    api = auth_client(loader)

    assert api.post(f"/api/loader/orders/{kept.pk}/dispatch/", {}, format="json").status_code == 200
    response = api.post(f"/api/loader/orders/{cleared.pk}/dispatch/", {"trailer_number": ""}, format="json")

    assert response.status_code == 200, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("612BEX13", "")
    kept.refresh_from_db()
    cleared.refresh_from_db()
    assert (kept.status, kept.trailer_number) == ("shipped", "07KG837PB")
    assert (cleared.status, cleared.trailer_number) == ("shipped", "")


def test_dispatch_does_not_clear_the_trailer_after_arrival(
    auth_client, loader, manager, product, dispatch_closed_orders, make_order,
):
    """После въезда прицеп заменить (и стереть) нельзя — отказ до закрытия AI-подсчёта."""
    order = make_order(
        product, status="arrived", truck_number="403BJN13", trailer_number="07KG837PB", truck_number_set_by=manager)

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/", {"trailer_number": ""}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "truck_number_locked"
    assert dispatch_closed_orders == []
    order.refresh_from_db()
    assert (order.status, order.trailer_number) == ("arrived", "07KG837PB")


def test_wagon_dispatch_notifies_without_the_wagon_number(auth_client, loader, product, make_order):
    order = make_order(product, transport_type="train", truck_number="00123456")

    assert auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 200

    assert list(Notification.objects.filter(client=order.client).values_list("text", flat=True)) == [
        f"Заказ №{order.pk} отгружен"
    ]


def test_queue_suggests_previous_pairs_of_the_client(auth_client, loader, product, make_order):
    previous = make_order(product, status="shipped", truck_number="07KG695ADT", trailer_number="07KG837PB")
    waiting = Order.objects.create(client=previous.client, status="confirmed")
    OrderItem.objects.create(order=waiting, product=product, quantity=1, unit_price="10.00")

    rows = auth_client(loader).get("/api/loader/queue/").data

    assert rows[0]["id"] == waiting.pk
    assert rows[0]["trailer_number"] == ""
    assert rows[0]["transport_suggestions"] == [{"truck_number": "07KG695ADT", "trailer_number": "07KG837PB"}]


def test_queue_query_count_does_not_grow_with_rows(
    auth_client, loader, product, django_assert_max_num_queries, make_order,
):
    def fill(count):
        for index in range(count):
            previous = make_order(product, status="shipped", truck_number=f"{100 + index}ABC01")
            # Номер указал клиент: transport_locked читает владельца номера.
            waiting = Order.objects.create(
                client=previous.client, status="confirmed",
                truck_number=f"{200 + index}ABC01", truck_number_set_by=previous.client.user)
            OrderItem.objects.create(order=waiting, product=product, quantity=1, unit_price="10.00")

    api = auth_client(loader)
    fill(2)
    with CaptureQueriesContext(connection) as small:
        assert len(api.get("/api/loader/queue/").data) == 2
    fill(5)
    with django_assert_max_num_queries(len(small.captured_queries)):
        assert len(api.get("/api/loader/queue/").data) == 7


def test_dispatch_accepts_the_clients_own_number_in_another_spelling(
    auth_client, loader, make_user, product, make_order,
):
    portal_user = make_user(username="portal-client", client=True)
    order = make_order(product, truck_number="07KG695ADT", truck_number_set_by=portal_user)

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "07 695 adt"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.truck_number_set_by) == ("07KG695ADT", portal_user)


@pytest.mark.parametrize("fields", [{"truck_number": "AB1"}, {"trailer_number": "12"}])
def test_dispatch_rejects_a_typo_before_closing_the_ai_count(
    auth_client, loader, product, fields, dispatch_closed_orders, make_order,
):
    """Опечатка в номере — 400 до закрытия AI-подсчёта: сессию камер она не останавливает."""
    order = make_order(product, status="loading")

    response = auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", fields, format="json")

    assert response.status_code == 400
    assert dispatch_closed_orders == []
    order.refresh_from_db()
    assert (order.status, order.truck_number, order.trailer_number) == ("loading", "", "")


def test_dispatch_keeps_an_unchanged_legacy_number(auth_client, loader, product, make_order):
    """Экран грузчика отправляет сохранённый номер обратно: старый текст — не опечатка."""
    order = make_order(product, truck_number="САМОВЫВОЗ")

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "САМОВЫВОЗ"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.status, order.truck_number) == ("shipped", "САМОВЫВОЗ")


def test_dispatch_refuses_a_trailer_on_the_clients_pair_before_closing_the_ai_count(
    auth_client, loader, make_user, product, dispatch_closed_orders, make_order, 
):
    """Пару указал клиент: прицеп к ней грузчик не дописывает, и сессию камер отказ не трогает."""
    portal_user = make_user(username="portal-owner", client=True)
    order = make_order(product, status="loading", truck_number="403BJN13", truck_number_set_by=portal_user)

    response = auth_client(loader).post(
        f"/api/loader/orders/{order.pk}/dispatch/",
        {"truck_number": "403BJN13", "trailer_number": "07KG837PB"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "forbidden"
    assert "прицеп" in response.data["detail"]
    assert dispatch_closed_orders == []
    order.refresh_from_db()
    assert (order.status, order.trailer_number) == ("loading", "")


def test_queue_marks_the_pair_the_client_entered(auth_client, loader, make_user, manager, product, make_order):
    """Номер клиента грузчик не меняет; после въезда пустой номер дописать можно."""
    portal_user = make_user(username="portal-pair", client=True)
    by_client = make_order(product, truck_number="403BJN13", truck_number_set_by=portal_user)
    by_staff = make_order(product, truck_number="612BEX13", truck_number_set_by=manager)
    arrived_empty = make_order(product, status="arrived")

    rows = {row["id"]: row for row in auth_client(loader).get("/api/loader/queue/").data}

    assert rows[by_client.pk]["transport_locked"] is True
    assert rows[by_staff.pk]["transport_locked"] is False
    assert rows[arrived_empty.pk]["transport_locked"] is False


def test_queue_search_finds_the_trailer(auth_client, loader, product, make_order):
    trailer = make_order(product, truck_number="07KG695ADT", trailer_number="07KG837PB")
    make_order(product, truck_number="403BJN13")

    rows = auth_client(loader).get("/api/loader/queue/", {"search": "07 kg 837"}).data

    assert [row["id"] for row in rows] == [trailer.pk]


def test_rollback_answer_keeps_the_number_suggestions(auth_client, loader, product, make_order):
    """Ответ отката применяется к строке очереди: чипы не пропадают до опроса."""
    previous = make_order(product, status="shipped", truck_number="07KG695ADT", trailer_number="07KG837PB")
    order = Order.objects.create(client=previous.client, status="confirmed")
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price="10.00")
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 BJN 13"}, format="json")

    response = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["transport_suggestions"] == [{"truck_number": "07KG695ADT", "trailer_number": "07KG837PB"}]


def test_queue_row_carries_the_clients_country_for_the_plate_field(auth_client, loader, product, make_order):
    """Страна клиента — страна номера по умолчанию в поле «Тягач» у грузчика."""
    waiting = make_order(product)
    Client.objects.filter(pk=waiting.client_id).update(country="Кыргызстан")

    rows = auth_client(loader).get("/api/loader/queue/").data

    assert rows[0]["client_country"] == "Кыргызстан"


def test_loader_ships_and_undoes_a_prepaid_order_keeping_the_money(auth_client, loader, boss, product, make_order):
    """Предоплата + кнопка грузчика: оплата переживает отгрузку и её отмену.

    ``_do_ship`` одновременно ставит статус оплаты по факту денег, пишет долг
    только на остаток и шлёт клиенту одно уведомление об отгрузке; отмена своей
    отгрузки не требует возврата денег — они остаются предоплатой.
    """
    from apps.orders.services import record_staff_payment

    order = make_order(product, quantity=2)
    record_staff_payment(order, "15000.00", boss, method="cash")
    api = auth_client(loader)

    response = api.post(
        f"/api/loader/orders/{order.pk}/dispatch/",
        {"truck_number": "07 kg 695 adt", "trailer_number": "07 kg 837 pb"},
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.status, order.payment_status) == ("shipped", "partial")
    debt = EventLog.objects.get(order=order, event_type="debt")
    assert debt.payload["amount"] == "5000.00"
    assert list(Notification.objects.filter(client=order.client).values_list("text", flat=True)) == [
        f"Заказ №{order.pk} отгружен"
    ]
    assert [row["can_rollback"] for row in api.get("/api/loader/history/").data] == [True]

    undo = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")

    assert undo.status_code == 200, undo.data
    order.refresh_from_db()
    assert (order.status, order.payment_status) == ("confirmed", "partial")
    assert str(order.paid_total) == "15000.00"
    assert [row["id"] for row in api.get("/api/loader/queue/").data] == [order.pk]
