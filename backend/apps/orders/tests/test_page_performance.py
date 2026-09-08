"""Page-level query budgets, using real serialization and distinct customers."""

from time import perf_counter

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.querysets import with_order_amounts
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def perf_user(user_with_perms):
    return user_with_perms(
        "performance",
        codes=[
            "orders.view",
            "payments.confirm",
            "payments.view",
            "reports.view",
            "clients.view",
        ],
    )


def seed_orders(count, offset=0):
    Department.objects.get_or_create(code="main", defaults={"name": "Основной"})
    product = Product.objects.create(name=f"Perf{offset}", color="Red", weight_kg=50)
    for n in range(offset, offset + count):
        client = Client.objects.create_with_user(first_name=f"Client{n}", phone="x")
        store = Store.objects.create(client=client, name=f"Store{n}")
        order = Order.objects.create(
            client=client, store=store, status="shipped", settlement_intent="debt"
        )
        OrderItem.objects.create(
            order=order, product=product, quantity=3, unit_price=100
        )
        OrderItem.objects.create(order=order, product=None, quantity=2, unit_price=50)
        Payment.objects.create(
            order=order, amount=100, status="confirmed", refunded_amount=10
        )
        Payment.objects.create(order=order, amount=20, status="received")


def measure(user, url):
    api = APIClient()
    api.force_authenticate(type(user).objects.get(pk=user.pk))
    started = perf_counter()
    with CaptureQueriesContext(connection) as queries:
        response = api.get(url)
        assert response.status_code == 200, response.data
    elapsed = perf_counter() - started
    if url == "/api/orders/department-summary/":
        assert sum(row["orders"] for row in response.data) == Order.objects.count()
    print(
        f"\nPERF {url}: {len(queries)} queries, {elapsed * 1000:.1f} ms, {len(response.content)} bytes"
    )
    return len(queries)


@pytest.mark.parametrize(
    "url",
    [
        "/api/orders/?page=1&page_size=50",
        "/api/orders/department-summary/",
        "/api/orders/payments-queue/",
        "/api/orders/payments-queue/?page=1&page_size=50",
        "/api/orders/payments-queue/?summary=1",
        "/api/clients/debts/",
        "/api/stores/",
        "/api/reports/summary/?section=income",
        "/api/payment-transactions/",
    ],
)
def test_page_queries_do_not_grow_per_customer(perf_user, url):
    seed_orders(2)
    small = measure(perf_user, url)
    seed_orders(18, offset=2)
    large = measure(perf_user, url)
    assert large == small, f"{url}: {small} -> {large} queries"


def test_page_benchmark(perf_user):
    seed_orders(300)
    for url in (
        "/api/orders/?page=1&page_size=50",
        "/api/orders/department-summary/",
        "/api/clients/debts/",
        "/api/orders/payments-queue/",
        "/api/orders/payments-queue/?summary=1",
    ):
        measure(perf_user, url)


def test_sql_amounts_match_model_after_refunds_and_unpriced_items():
    seed_orders(1)
    order = Order.objects.get()
    OrderItem.objects.create(order=order, product=None, quantity=9, unit_price=None)
    Payment.objects.create(
        order=order, amount=200, refunded_amount=30, status="confirmed"
    )
    Payment.objects.create(order=order, amount=900, status="rejected")
    row = with_order_amounts(Order.objects.all()).get()
    assert (row.amount_total, row.amount_paid, row.amount_remaining) == (
        order.total_amount,
        order.paid_total,
        order.remaining_amount,
    )


def test_queue_summary_matches_full_queue_and_pages(perf_user):
    seed_orders(8)
    api = APIClient()
    api.force_authenticate(perf_user)
    full = api.get("/api/orders/payments-queue/").json()
    totals = api.get("/api/orders/payments-queue/?summary=1").json()
    assert totals == [
        {"currency": "KZT", "method": "cash", "amount": "160.00", "count": 8}
    ]
    page = api.get("/api/orders/payments-queue/?page=1&page_size=3").json()
    assert page["count"] == len(full) == 8
    assert [row["id"] for row in page["results"]] == [row["id"] for row in full[:3]]
    assert page["next"] is not None
    assert (
        api.get("/api/orders/payments-queue/?summary=1&department=missing").json() == []
    )


def test_order_search_sort_and_pagination_cover_the_complete_selection(perf_user):
    seed_orders(8)
    api = APIClient()
    api.force_authenticate(perf_user)
    oldest = Order.objects.order_by("pk").first()
    Order.objects.filter(pk=oldest.pk).update(status="loading", truck_number="934PPB13")
    url = "/api/orders/?ordering=-id&page_size=3&page=1"
    page = api.get(url).json()
    assert page["count"] == 8
    assert page["results"][0]["id"] == oldest.pk  # active first, globally
    other = api.get(page["next"]).json()
    assert not {r["id"] for r in page["results"]} & {r["id"] for r in other["results"]}
    search = api.get(
        "/api/orders/", {"search": "934 PPB 13", "page": 1, "ordering": "-id"}
    ).json()
    assert [row["id"] for row in search["results"]] == [oldest.pk]
    names = api.get("/api/orders/", {"search": "Client0", "page": 1}).json()
    assert names["count"] == 1
    assert names["results"][0]["id"] == oldest.pk
    for ordering in ("amount", "-amount", "client", "-client", "created", "status"):
        assert (
            api.get("/api/orders/", {"ordering": ordering, "page": 1}).status_code
            == 200
        )
    assert api.get("/api/orders/?ordering=client__password").status_code == 400


def test_optimized_lists_and_totals_keep_client_ownership_scope(perf_user):
    seed_orders(2)
    department = Department.objects.create(code="restricted", name="Restricted")
    visible = Client.objects.order_by("pk").first()
    Client.objects.filter(pk=visible.pk).update(department=department)
    perf_user.employee.sales_department = department
    perf_user.employee.save(update_fields=["sales_department"])
    api = APIClient()
    api.force_authenticate(perf_user)
    orders = api.get("/api/orders/?page=1&search=Client&ordering=amount").json()
    assert orders["count"] == 1
    assert orders["results"][0]["client"] == visible.pk
    assert api.get("/api/orders/payments-queue/?summary=1").json() == [
        {"currency": "KZT", "method": "cash", "amount": "20.00", "count": 1}
    ]
    debts = api.get("/api/clients/debts/").json()
    assert [row["client_id"] for row in debts] == [visible.pk]
    assert debts[0]["debt_total"] == "310.00"
