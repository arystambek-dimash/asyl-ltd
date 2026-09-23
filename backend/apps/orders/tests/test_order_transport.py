"""Номер тягача и прицепа: один сервис orders/transport.set_order_transport."""
import pytest
from rest_framework.exceptions import ValidationError

from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.notifications.models import Notification
from apps.orders.models import Order
from apps.orders.services import set_transport_type, set_truck_number
from apps.orders.transport import set_order_transport

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_user(make_user):
    return make_user(username="cli", client=True)


@pytest.fixture
def order(portal_user):
    client = Client.objects.create_with_user(
        user=portal_user, first_name="Азамат", last_name="К", phone="transport-1")
    return Order.objects.create(client=client, status="confirmed")


def _notes(order):
    return list(Notification.objects.filter(client=order.client).values_list("text", flat=True))


def test_staff_sets_pair_normalized_logs_and_notifies(order, manager):
    updated, changed = set_order_transport(
        order, manager, truck=" 07 kg 695 adt ", trailer="07-kg-837-pb")

    assert changed is True
    assert (updated.truck_number, updated.trailer_number) == ("07KG695ADT", "07KG837PB")
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("07KG695ADT", "07KG837PB")
    assert order.truck_number_set_by == manager
    event = EventLog.objects.get(order=order)
    assert {key: event.payload[key] for key in ("truck", "trailer", "previous")} == {
        "truck": "07KG695ADT",
        "trailer": "07KG837PB",
        "previous": {"truck": "", "trailer": ""},
    }
    assert _notes(order) == [f"Заказ №{order.pk}: машина 07 KG 695 ADT, прицеп 07 KG 837 PB"]


def test_same_pair_in_other_spelling_is_a_noop(order, portal_user, manager):
    set_order_transport(order, portal_user, truck="403BJN13", trailer="07KG837PB")
    events = EventLog.objects.filter(order=order).count()

    # Сотрудник видит «403 bjn 13» и прицеп без «KG» — это не смена номера,
    # поэтому правило владельца не срабатывает и ничего не пишется.
    updated, changed = set_order_transport(order, manager, truck="403 bjn 13", trailer="07837pb")

    assert changed is False
    order.refresh_from_db()
    assert order.truck_number_set_by == portal_user
    assert EventLog.objects.filter(order=order).count() == events
    assert _notes(order) == []


def test_country_letters_of_a_known_plate_are_not_a_country_marker(order, manager):
    """«B1234UZ» — старый киргизский номер, а не «B1234» с отметкой Узбекистана."""
    set_order_transport(order, manager, truck="B1234")

    updated, changed = set_order_transport(order, manager, truck="B1234UZ")

    assert changed is True
    order.refresh_from_db()
    assert order.truck_number == "B1234UZ"


def test_client_change_does_not_notify_client(order, portal_user):
    set_order_transport(order, portal_user, truck="403BJN13")
    assert _notes(order) == []


def test_notify_can_be_disabled(order, manager):
    set_order_transport(order, manager, truck="403BJN13", notify_client=False)
    assert _notes(order) == []


def test_client_owned_pair_is_not_changed_by_staff(order, portal_user, manager):
    set_order_transport(order, portal_user, truck="403BJN13")

    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck="403BJN13", trailer="07KG837PB")

    assert exc.value.detail["code"] == "forbidden"
    # Сотрудник менял только прицеп — отказ говорит о прицепе, а не о «смене машины».
    assert exc.value.detail["detail"] == "Номер транспорта указал клиент — прицеп к нему тоже указывает клиент"
    order.refresh_from_db()
    assert order.trailer_number == ""


@pytest.mark.parametrize(
    ("owner", "user", "message"),
    [("portal_user", "manager", "Номер транспорта указал клиент — изменить его может только клиент"),
     ("manager", "portal_user", "Номер транспорта указал менеджер — изменить его может только менеджер")],
)
def test_owner_refusal_names_who_entered_the_number(request, order, owner, user, message):
    owner, user = request.getfixturevalue(owner), request.getfixturevalue(user)
    Order.objects.filter(pk=order.pk).update(truck_number="403BJN13", truck_number_set_by=owner)

    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, user, truck="612BEX13")

    assert (exc.value.detail["code"], exc.value.detail["detail"]) == ("forbidden", message)


@pytest.mark.parametrize("status", ["arrived", "loading", "loaded", "shipped"])
def test_replacing_a_number_is_locked_after_arrival(order, manager, status):
    Order.objects.filter(pk=order.pk).update(
        status=status, truck_number="403BJN13", truck_number_set_by=manager)

    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck="612BEX13")

    assert exc.value.detail["code"] == "truck_number_locked"


def test_empty_number_can_be_filled_after_arrival(order, manager):
    Order.objects.filter(pk=order.pk).update(status="arrived")

    set_order_transport(order, manager, truck="403BJN13")
    set_order_transport(order, manager, truck="403BJN13", trailer="07KG837PB")

    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "07KG837PB")
    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck="403BJN13", trailer="")
    assert exc.value.detail["code"] == "truck_number_locked"


@pytest.mark.parametrize("status", ["arrived", "loading", "loaded", "shipped"])
def test_number_filled_on_site_is_not_sent_to_the_client(order, manager, status):
    """Машина на территории — её номер клиенту не показывается (решение
    владельца, как в портале), поэтому и уведомления о дописанном номере нет."""
    Order.objects.filter(pk=order.pk).update(
        status=status, truck_number="403BJN13", truck_number_set_by=manager)

    _, changed = set_order_transport(order, manager, truck="403BJN13", trailer="07KG837PB")

    assert changed is True
    order.refresh_from_db()
    assert order.trailer_number == "07KG837PB"
    assert _notes(order) == []


def test_open_ai_session_locks_replacement(order, manager):
    Order.objects.filter(pk=order.pk).update(truck_number="403BJN13", truck_number_set_by=manager)
    AiCountingSession.objects.create(
        order=order, camera="cam2", status=AiCountingSession.STARTING, started_by=manager)

    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck="612BEX13")

    assert exc.value.detail["code"] == "truck_number_locked"


@pytest.mark.parametrize(
    ("truck", "trailer", "field"),
    [("777", None, "truck_number"), ("403BJN13", "AAA", "trailer_number"),
     ("X" * 31, None, "truck_number")],
)
def test_impossible_numbers_are_rejected(order, manager, truck, trailer, field):
    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck=truck, trailer=trailer)
    assert field in exc.value.detail


def test_unchanged_legacy_number_does_not_block_a_trailer(order, manager):
    Order.objects.filter(pk=order.pk).update(truck_number="САМОВЫВОЗ")

    set_order_transport(order, manager, truck="САМОВЫВОЗ", trailer="07KG837PB")

    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("САМОВЫВОЗ", "07KG837PB")


def test_wagon_takes_eight_digits_and_no_trailer(order, manager):
    Order.objects.filter(pk=order.pk).update(transport_type="train")

    set_order_transport(order, manager, truck="0012 3456")
    order.refresh_from_db()
    assert order.truck_number == "00123456"
    assert _notes(order)[-1] == f"Заказ №{order.pk}: вагон 00123456"

    with pytest.raises(ValidationError) as exc:
        set_order_transport(order, manager, truck="00123456", trailer="07KG837PB")
    assert "trailer_number" in exc.value.detail
    with pytest.raises(ValidationError):
        set_order_transport(order, manager, truck="403BJN13")


def test_legacy_truck_helper_keeps_trailer(order, manager):
    set_order_transport(order, manager, truck="403BJN13", trailer="07KG837PB")

    set_truck_number(order, "612 bex 13", manager)

    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("612BEX13", "07KG837PB")


def test_switching_to_train_clears_trailer(order, manager):
    Order.objects.filter(pk=order.pk).update(trailer_number="07KG837PB")

    set_transport_type(order, "train", manager)

    order.refresh_from_db()
    assert (order.transport_type, order.trailer_number) == ("train", "")
