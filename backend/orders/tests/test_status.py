from orders.models import Order


def test_loaded_status_between_loading_and_shipped():
    s = Order.STATUSES
    assert "loaded" in s
    assert s.index("loading") < s.index("loaded") < s.index("shipped")
