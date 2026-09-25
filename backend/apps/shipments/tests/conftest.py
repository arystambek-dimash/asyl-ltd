import pytest


@pytest.fixture
def trucks_loader(user_with_perms):
    return user_with_perms("trucks-loader", codes=["loader.view", "loader.confirm", "loader.trucks"])


@pytest.fixture
def wagons_loader(user_with_perms):
    return user_with_perms("wagons-loader", codes=["loader.view", "loader.confirm", "loader.wagons"])


@pytest.fixture
def product(boss, make_product):
    """«Д1с» со 100 мешками на основном складе."""
    from apps.warehouse.services import receive_stock

    item = make_product(name="Д1с")
    receive_stock(item, 100, boss)
    return item


@pytest.fixture
def make_order(db):
    """Заказ клиента «ИП Мурат» на ``quantity`` мешков товара по 10 000."""
    from apps.clients.models import Client
    from apps.orders.models import Order, OrderItem

    def _make(product, status="confirmed", quantity=2, **fields):
        client = Client.objects.create_with_user(
            first_name="Мурат", phone="+7 (778) 535-22-10", company_name="ИП Мурат"
        )
        order = Order.objects.create(client=client, status=status, **fields)
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="10000.00")
        return order

    return _make


@pytest.fixture
def dispatch_closed_orders(monkeypatch):
    """Закрытие AI-подсчёта при отгрузке подменено: список заказов, чью сессию закрыли."""
    from apps.cameras import counting

    closed = []
    monkeypatch.setattr(counting, "close_session_for_dispatch", lambda order, user: closed.append(order.pk))
    return closed
