import pytest
from unittest.mock import patch
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, StatusChangeRequest
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock
from apps.cameras.models import AiCountingSession
from apps.cameras import recordings
from apps.eventlog.models import EventLog
from apps.orders import services
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _order():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    return Order.objects.create(client=c, status="confirmed")


def test_editor_changes_status_immediately(manager):
    # manager has orders.edit
    o = _order()
    res = services.request_status_change(o, "shipped", manager)
    assert res["applied"] is True
    o.refresh_from_db()
    assert o.status == "shipped"
    assert StatusChangeRequest.objects.count() == 0


def test_operator_creates_request(operator):
    # operator lacks orders.edit
    o = _order()
    res = services.request_status_change(o, "shipped", operator)
    assert res["applied"] is False
    o.refresh_from_db()
    assert o.status == "confirmed"  # unchanged
    req = StatusChangeRequest.objects.get()
    assert req.status == "pending"
    assert req.to_status == "shipped"


def test_approve_request_applies_status(operator, boss):
    o = _order()
    services.request_status_change(o, "shipped", operator)
    req = StatusChangeRequest.objects.get()
    services.approve_status_change(req, boss)
    o.refresh_from_db(); req.refresh_from_db()
    assert o.status == "shipped"
    assert req.status == "approved"
    assert req.decided_by == boss


def test_reject_request_keeps_status(operator, boss):
    o = _order()
    services.request_status_change(o, "shipped", operator)
    req = StatusChangeRequest.objects.get()
    services.reject_status_change(req, boss)
    o.refresh_from_db(); req.refresh_from_db()
    assert o.status == "confirmed"
    assert req.status == "rejected"


def test_double_decide_rejected(operator, boss):
    o = _order()
    services.request_status_change(o, "shipped", operator)
    req = StatusChangeRequest.objects.get()
    services.approve_status_change(req, boss)
    with pytest.raises(ValidationError):
        services.reject_status_change(req, boss)


def test_set_status_endpoint_operator_gets_202(auth_client, operator):
    o = _order()
    r = auth_client(operator).post(f"/api/orders/{o.id}/set-status/", {"status": "shipped"}, format="json")
    assert r.status_code == 202
    assert r.data["applied"] is False
    assert r.data["request"]["to_status"] == "shipped"
    assert r.data["request"]["to_status_label"] == "Отгружено"


def test_completed_shipment_rollback_restores_stock_deletes_video_and_audits(
        auth_client, boss):
    product = Product.objects.create(name="Архив", color="Red", weight_kg="50")
    receive_stock(product, 20, boss)
    order = _order()
    OrderItem.objects.create(order=order, product=product, quantity=7, unit_price="10")
    services.request_status_change(order, "shipped", boss, bags_loaded=6)
    session = AiCountingSession.objects.create(
        order=order, camera="cam2", status=AiCountingSession.CLOSED,
        started_by=boss, closed_by=boss, ended_at=order.shipment.shipped_at,
        recording_stream="cam2ai", final_total=6,
    )
    assert StockItem.objects.get(product=product).bags == 13

    with patch("apps.cameras.recordings.delete_session_segments", return_value=2) as delete:
        response = auth_client(boss).post(
            f"/api/orders/{order.id}/rollback-shipment/",
            {"status": "confirmed", "reason": "Ошибочно выбран заказ"},
            format="json",
        )

    assert response.status_code == 200
    order.refresh_from_db(); session.refresh_from_db()
    assert order.status == "confirmed"
    assert StockItem.objects.get(product=product).bags == 20
    assert not Shipment.objects.filter(order=order).exists()
    assert session.recording_stream == ""
    delete.assert_called_once()
    event = EventLog.objects.get(event_type="shipment_rollback", order=order)
    assert event.user == boss
    assert event.payload["reason"] == "Ошибочно выбран заказ"
    assert event.payload["recording_segments_deleted"] == 2


def test_shipment_rollback_continues_when_camera_pc_is_unavailable(
        auth_client, boss):
    product = Product.objects.create(name="Локальная запись", color="Blue", weight_kg="50")
    receive_stock(product, 12, boss)
    order = _order()
    OrderItem.objects.create(order=order, product=product, quantity=4, unit_price="10")
    services.request_status_change(order, "shipped", boss, bags_loaded=4)
    session = AiCountingSession.objects.create(
        order=order, camera="cam3", status=AiCountingSession.CLOSED,
        started_by=boss, closed_by=boss, ended_at=order.shipment.shipped_at,
        recording_stream="cam3ai", final_total=4,
    )

    with patch(
        "apps.cameras.recordings.delete_session_segments",
        side_effect=recordings.RecordingUnavailable("offline"),
    ):
        response = auth_client(boss).post(
            f"/api/orders/{order.id}/rollback-shipment/",
            {"status": "confirmed", "reason": "Повторная обработка заказа"},
            format="json",
        )

    assert response.status_code == 200
    order.refresh_from_db(); session.refresh_from_db()
    assert order.status == "confirmed"
    assert StockItem.objects.get(product=product).bags == 12
    assert session.recording_stream == "cam3ai"
    assert "сроку хранения" in session.error
    event = EventLog.objects.get(event_type="shipment_rollback", order=order)
    assert event.payload["recording_cleanup_pending_session_ids"] == [session.pk]


def test_shipment_rollback_requires_permission_and_reason(auth_client, operator, boss):
    order = _order()
    services.request_status_change(order, "shipped", boss, bags_loaded=0)
    forbidden = auth_client(operator).post(
        f"/api/orders/{order.id}/rollback-shipment/",
        {"status": "confirmed", "reason": "Неверный заказ"}, format="json")
    assert forbidden.status_code == 403
    missing_reason = auth_client(boss).post(
        f"/api/orders/{order.id}/rollback-shipment/",
        {"status": "confirmed", "reason": ""}, format="json")
    assert missing_reason.status_code == 400
    order.refresh_from_db()
    assert order.status == "shipped"


def test_set_status_endpoint_editor_applies(auth_client, manager):
    o = _order()
    r = auth_client(manager).post(f"/api/orders/{o.id}/set-status/", {"status": "shipped"}, format="json")
    assert r.status_code == 200
    assert r.data["applied"] is True
    assert r.data["order"]["status"] == "shipped"


def test_approve_endpoint(auth_client, operator, manager):
    o = _order()
    services.request_status_change(o, "shipped", operator)
    req = StatusChangeRequest.objects.get()
    r = auth_client(manager).post(f"/api/orders/{o.id}/status-requests/{req.id}/approve/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "shipped"


def test_missing_nested_status_request_returns_404(auth_client, manager):
    o = _order()

    response = auth_client(manager).post(
        f"/api/orders/{o.id}/status-requests/999999/approve/")

    assert response.status_code == 404


def test_regular_editor_cannot_choose_internal_status(manager):
    # Внутренние этапы (въезд, идёт погрузка, завершение погрузки) ставят
    # только бизнес-процессы — вручную они недоступны.
    for internal in ("arrived", "loading", "loaded"):
        o = _order()
        with pytest.raises(ValidationError) as exc:
            services.request_status_change(o, internal, manager)
        assert exc.value.detail["code"] == "status_not_available"


def test_regular_editor_endpoint_rejects_internal_status(auth_client, manager):
    o = _order()
    response = auth_client(manager).post(
        f"/api/orders/{o.id}/set-status/", {"status": "arrived"}, format="json")
    assert response.status_code == 400
    assert "Доступны статусы" in str(response.data["detail"])
    o.refresh_from_db()
    assert o.status == "confirmed"


def test_superuser_can_choose_internal_status(make_user):
    root = make_user(username="root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])
    o = _order()
    result = services.request_status_change(o, "arrived", root)
    assert result["applied"] is True
    o.refresh_from_db()
    assert o.status == "arrived"


def test_manual_completed_status_runs_full_shipping_flow(auth_client, manager):
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg="50", price="100.00")
    receive_stock(product, 100, manager)
    order = _order()
    OrderItem.objects.create(
        order=order, product=product, quantity=50, unit_price="100.00")

    response = auth_client(manager).post(
        f"/api/orders/{order.id}/set-status/",
        {"status": "shipped", "bags_loaded": 47},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.loading_camera == ""
    assert order.shipment.bags_loaded == 47
    assert order.shipment.shipped_at is not None
    assert StockItem.objects.get(product=product).bags == 50


def test_manual_completed_without_count_uses_order_quantity(auth_client, manager):
    product = Product.objects.create(
        name="Мука", color="Blue", weight_kg="25", price="100.00")
    receive_stock(product, 20, manager)
    order = _order()
    OrderItem.objects.create(
        order=order, product=product, quantity=12, unit_price="100.00")

    response = auth_client(manager).post(
        f"/api/orders/{order.id}/set-status/", {"status": "shipped"}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.shipment.bags_loaded == 12


def test_manual_completion_never_binds_camera(auth_client, manager):
    order = _order()

    response = auth_client(manager).post(
        f"/api/orders/{order.id}/set-status/",
        {"status": "shipped", "bags_loaded": 0},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.loading_camera == ""
    assert AiCountingSession.objects.filter(order=order).count() == 0


def test_manual_completion_rejects_open_ai_session(auth_client, manager):
    order = _order()
    order.status = "loading"
    order.loading_camera = "cam3"
    order.save(update_fields=["status", "loading_camera"])
    AiCountingSession.objects.create(
        order=order, camera="cam3", status=AiCountingSession.ACTIVE,
        started_by=manager,
    )

    response = auth_client(manager).post(
        f"/api/orders/{order.id}/set-status/",
        {"status": "shipped", "bags_loaded": 10},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    order.refresh_from_db()
    assert order.status == "loading"
