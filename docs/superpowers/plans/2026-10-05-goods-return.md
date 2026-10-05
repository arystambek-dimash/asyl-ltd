# Возврат товара от клиента — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кнопка «Возврат» на /orders: клиент привёз мешки — система раскладывает их по его отгруженным заказам (в счёт долга или деньгами из кассы), мешки приходят на склад.

**Architecture:** `OrderItem.returned_quantity` + записи `GoodsReturn`/`GoodsReturnLine`; сумма заказа считается за `quantity − returned_quantity` в трёх общих местах. Раскладка и проведение — `apps/orders/goods_returns.py` (по образцу `debt_payments.py`), ручка `POST /api/clients/{id}/goods-return/`. Фронт — окно `GoodsReturnModal` с общим выбором клиента, вынесенным из `OrderForm`.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest; Next.js (App Router) + TypeScript + Tailwind v4, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-05-goods-return-design.md`

**Отклонения при реализации (по решениям владельца):** валюта не выбирается — берётся из каждого заказа, ответ даёт `amounts` по валютам; GET той же ручки отдаёт муку клиента с лимитами; бейдж «Возвращён»; возврат виден в портале. Актуальное описание — в спеке.

## Global Constraints

- Правила проекта — `CLAUDE.md` в корне: найди существующее, не дублируй, без мусора.
- **Не коммитить и не пушить** — только по команде владельца («пушни»), одним коммитом. Шагов «Commit» в задачах нет.
- pytest — всегда со своей базой: `DB_NAME=asyl_goods_return backend/.venv/bin/pytest …` (из `backend/`: `DB_NAME=asyl_goods_return .venv/bin/pytest …`).
- Деньги: валюты не складывать; сумма заказа — только через `Order.total_amount` / `querysets.item_value_sum`; долг — через `orders/debt.py`.
- Корзина (`deleted_at`) не участвует нигде: кандидаты — через `Order.objects` (LiveOrderManager).
- Порядок раскладки — от новой отгрузки к старой: `sorted(orders, key=debt.oldest_debt_first, reverse=True)`.
- Права: ручка — `orders.edit`; режим `cash` дополнительно `payments.confirm`.
- Тексты ошибок — по-русски, с `code`.
- Фронт из `frontend/`: `npm run check` (prettier → eslint → tsc → vitest) и `npx knip --no-progress` без новых находок.

## Карта файлов

Backend:
- Modify `backend/apps/orders/models.py` — `OrderItem.returned_quantity`, `OrderItem.sold_quantity`, `Order.total_amount` по `sold_quantity`, модели `GoodsReturn`, `GoodsReturnLine`.
- Create `backend/apps/orders/migrations/0048_goods_return.py` (makemigrations).
- Modify `backend/apps/orders/querysets.py` — `item_value_sum()` за вычетом возврата.
- Modify `backend/apps/common/money.py` — общий `money_text()` (перенос `_money_text` из `debt_payments.py`).
- Modify `backend/apps/orders/debt_payments.py` — пользоваться `money_text`.
- Modify `backend/apps/warehouse/models.py` — причина `client_return`; Create миграция `0012_…` (makemigrations).
- Modify `backend/apps/warehouse/services.py` — `return_stock()`.
- Create `backend/apps/orders/goods_returns.py` — раскладка и проведение.
- Modify `backend/apps/clients/views.py` — action `goods_return` + право.
- Modify `backend/apps/orders/services.py` (`replace_items`), `backend/apps/shipments/services.py` (`rollback_shipment`) — отказ `order_has_returns`.
- Modify `backend/apps/orders/serializers.py` — `returned_quantity` в `OrderItemSerializer`.
- Modify `backend/apps/clients/reports/statements/presentation.py`, `xlsx.py`, `pdf.py` — возврат в выписке.
- Tests: `backend/apps/orders/tests/test_goods_returns.py` (новый), `backend/apps/warehouse/tests/test_return_stock.py` (новый), правки существующих при необходимости.

Frontend:
- Create `frontend/src/components/orders/order-form-parts.tsx` — типы опций формы, `EMPTY_FORM_OPTIONS`, `SectionTitle`, `ClientPicker`.
- Modify `frontend/src/components/order-form.tsx` — пользоваться частями.
- Create `frontend/src/components/orders/goods-return-modal.tsx` (+ `.test.tsx`).
- Modify `frontend/src/app/orders/page.tsx` — кнопка «Возврат» и окно.
- Modify `frontend/src/app/orders/[id]/page.tsx` — «возврат N» у позиции, сумма за остаток.
- Modify `frontend/src/lib/types.ts` — `OrderItem.returned_quantity`.

---

### Task 1: Счётчик возврата в позиции и сумма заказа за остаток

**Files:**
- Modify: `backend/apps/orders/models.py` (класс `Order.total_amount` ~стр. 183; класс `OrderItem` ~стр. 220)
- Modify: `backend/apps/orders/querysets.py:62-64` (`item_value_sum`)
- Create: `backend/apps/orders/migrations/0048_goods_return.py` (makemigrations)
- Test: `backend/apps/orders/tests/test_goods_returns.py`

**Interfaces:**
- Produces: `OrderItem.returned_quantity: int` (поле), `OrderItem.sold_quantity -> int` (property), модели `GoodsReturn(client, currency, settlement, warehouse, created_by, created_at)` и `GoodsReturnLine(goods_return, order_item, bags, unit_price, amount)` с `related_name` `lines` / `return_lines`; `GoodsReturn.SETTLEMENTS = [("debt", "В счёт долга"), ("cash", "Из кассы")]`.

- [ ] **Step 1: Тест — сумма заказа за вычетом возврата во всех трёх местах**

Create `backend/apps/orders/tests/test_goods_returns.py`:

```python
"""«Возврат» по клиенту: мешки раскладываются по отгруженным заказам — от новой отгрузки к старой."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.querysets import order_remaining_by_id, with_order_amounts
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def _client(department, *, currency="KZT"):
    return Client.objects.create_with_user(
        first_name="Клиент", phone="+7 (705) 565-65-65", department=department, currency=currency,
    )


def _flour(name="Первый сорт DIKHAN 50кг", color="Blue"):
    product, _ = Product.objects.get_or_create(name=name, color=color, weight_kg=Decimal("50"))
    return product


def _shipped(client, items, *, currency="KZT", shipped=None):
    """Отгруженный заказ: ``items`` — [(товар, мешков, цена)]."""
    order = Order.objects.create(
        client=client, status="shipped", currency=currency, department=client.department.code,
    )
    for product, quantity, price in items:
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price=Decimal(price))
    Shipment.objects.create(order=order, shipped_at=shipped or timezone.now())
    return order


def test_order_amount_counts_only_bags_kept_by_the_client(departments):
    order = _shipped(_client(departments[0]), [(_flour(), 100, "3000")])
    OrderItem.objects.filter(order=order).update(returned_quantity=30)

    order = Order.objects.prefetch_related("items", "payments").get(pk=order.pk)
    item = order.items.get()
    assert (item.quantity, item.returned_quantity, item.sold_quantity) == (100, 30, 70)
    assert order.ordered_bags == 100  # отгружено — физика, не меняется
    assert order.total_amount == Decimal("210000")
    assert with_order_amounts(Order.objects.filter(pk=order.pk)).get().amount_total == Decimal("210000")
    assert order_remaining_by_id(Order.objects.filter(pk=order.pk)) == {order.pk: Decimal("210000")}
```

- [ ] **Step 2: Запустить — падает**

Run (из `backend/`): `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q`
Expected: FAIL — `returned_quantity` не существует (FieldError).

- [ ] **Step 3: Поле, property и модели**

В `backend/apps/orders/models.py`, класс `OrderItem`, после `unit_price`:

```python
    # Мешки, которые клиент вернул («Возврат», orders/goods_returns.py): отгружено
    # остаётся ``quantity``, деньги считаются за :attr:`sold_quantity`.
    # db_default: откат релиза вставляет позиции без этой колонки.
    returned_quantity = models.PositiveIntegerField(default=0, db_default=0)
```

и рядом с `product_label`:

```python
    @property
    def sold_quantity(self) -> int:
        """Мешки, оставшиеся у клиента: за них и считаются деньги."""
        return self.quantity - self.returned_quantity
```

`Order.total_amount` — заменить `i.quantity` на `i.sold_quantity`:

```python
    @property
    def total_amount(self) -> Decimal:
        # Единственный источник суммы — договорная цена, зафиксированная в заказе.
        # У товара общей цены нет; неподтверждённая позиция пока стоит 0.
        # Возвращённые мешки (``returned_quantity``) в сумму не входят.
        return sum(
            (i.sold_quantity * (i.unit_price if i.unit_price is not None else Decimal("0"))
             for i in self.items.all()),
            Decimal("0"),
        )
```

В конец `models.py` (после последней модели модуля):

```python
class GoodsReturn(models.Model):
    """«Возврат»: клиент привёз мешки, они разложены по его отгруженным заказам.

    Строки — :class:`GoodsReturnLine`; позиция заказа копит
    ``OrderItem.returned_quantity``. ``settlement`` — что с деньгами: ``debt``
    уменьшает долг, ``cash`` — касса отдала деньги кассовыми возвратами оплат.
    """

    SETTLEMENTS = [("debt", "В счёт долга"), ("cash", "Из кассы")]

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="goods_returns")
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    settlement = models.CharField(max_length=10, choices=SETTLEMENTS)
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.PROTECT, related_name="goods_returns")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)


class GoodsReturnLine(models.Model):
    """Сколько мешков возврата легло на позицию заказа и по какой цене."""

    goods_return = models.ForeignKey(GoodsReturn, on_delete=models.CASCADE, related_name="lines")
    # CASCADE: окончательная очистка корзины удаляет позиции заказа — она не должна падать.
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="return_lines")
    bags = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
```

Проверь импорты в начале `models.py`: `settings` и `CURRENCY_CHOICES` (из `apps.common.money`) уже используются моделями `Order`/`Payment` — если какого-то нет, добавь.

В `backend/apps/orders/querysets.py`:

```python
def item_value_sum():
    """Сумма позиций заказа за мешки, оставшиеся у клиента (без возврата); без цены — ноль, как в модели."""
    return Sum(
        (F("quantity") - F("returned_quantity")) * Coalesce("unit_price", ZERO_MONEY),
        output_field=MONEY,
    )
```

- [ ] **Step 4: Миграция**

Run (из `backend/`): `.venv/bin/python manage.py makemigrations orders --name goods_return`
Expected: `0048_goods_return.py` — AddField `returned_quantity` (с `db_default=0`), CreateModel `GoodsReturn`, `GoodsReturnLine`.

- [ ] **Step 5: Тест проходит**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q`
Expected: PASS.

---

### Task 2: Склад — движение «Возврат от клиента»

**Files:**
- Modify: `backend/apps/warehouse/models.py:77-84` (`StockMovement.REASONS`)
- Modify: `backend/apps/warehouse/services.py` (после `receive_stock`)
- Create: миграция warehouse (makemigrations, `0012_…`)
- Test: `backend/apps/warehouse/tests/test_return_stock.py`

**Interfaces:**
- Produces: `return_stock(product, bags: int, user, warehouse, note: str = "") -> StockItem` — под блокировкой, движение `reason="client_return"`, `StockReceipt` не создаёт.

- [ ] **Step 1: Тест**

Create `backend/apps/warehouse/tests/test_return_stock.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError

from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.services import return_stock

pytestmark = pytest.mark.django_db


def test_returned_bags_come_back_as_client_return_not_receipt(boss, make_product):
    flour = make_product(name="Д1с")
    main = Warehouse.objects.get(code="main")

    return_stock(flour, 30, boss, main, note="Возврат товара №1")

    assert StockItem.objects.get(product=flour, warehouse=main).bags == 30
    movement = StockMovement.objects.get(product=flour)
    assert (movement.delta, movement.reason, movement.note) == (30, "client_return", "Возврат товара №1")
    assert movement.get_reason_display() == "Возврат от клиента"
    assert not StockReceipt.objects.exists()  # не производство


def test_return_needs_positive_bags(boss, make_product):
    with pytest.raises(ValidationError):
        return_stock(make_product(name="Д1с"), 0, boss, Warehouse.objects.get(code="main"))
```

- [ ] **Step 2: Падает**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/warehouse/tests/test_return_stock.py -q`
Expected: FAIL — `ImportError: cannot import name 'return_stock'`.

- [ ] **Step 3: Реализация**

`StockMovement.REASONS` — добавить последней строкой:

```python
        ("client_return", "Возврат от клиента"),
```

В `backend/apps/warehouse/services.py` после `receive_stock`:

```python
@transaction.atomic
def return_stock(product, bags, user, warehouse, note=""):
    """Мешки, которые вернул клиент, — на склад движением «Возврат от клиента».

    Не «Приёмка» (``StockReceipt`` — выпуск производства): возврат не должен
    искажать производство. ``warehouse`` — уже проверенный склад.
    """
    if bags <= 0:
        raise ValidationError(
            {"detail": "Количество мешков должно быть больше нуля", "code": "invalid_bags"}
        )
    item = _locked_stock_item(product, warehouse, create=True)
    _post_movement(item, bags, "client_return", user, note)
    return item
```

(`transaction` уже импортирован в модуле — проверь; `receive_stock` рядом использует те же `_locked_stock_item`/`_post_movement`.)

- [ ] **Step 4: Миграция**

Run: `.venv/bin/python manage.py makemigrations warehouse --name client_return_reason`
Expected: AlterField `stockmovement.reason`.

- [ ] **Step 5: Проходит**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/warehouse/tests/test_return_stock.py -q`
Expected: PASS.

---

### Task 3: Общий `money_text` (вынос из «Внести оплату»)

**Files:**
- Modify: `backend/apps/common/money.py`
- Modify: `backend/apps/orders/debt_payments.py:100-103` и места вызова `_money_text`

**Interfaces:**
- Produces: `money_text(value: Decimal, currency: str) -> str` — «3 150 000 ₸».

- [ ] **Step 1: Перенос**

В `backend/apps/common/money.py` (после `money_string`):

```python
def money_text(value, currency: str) -> str:
    """«3 150 000 ₸» — сумма в тексте для человека."""
    from .text import group_digits

    return f"{group_digits(value)} {CURRENCY_SIGNS.get(currency, currency)}"
```

Если `apps/common/text.py` не импортирует `money.py` (проверь `rg -n "import" apps/common/text.py`), импорт `group_digits` поднять в начало модуля вместо локального.

В `debt_payments.py`: удалить `_money_text`, импортировать `money_text` из `apps.common.money`, заменить вызовы `_money_text(` → `money_text(`; убрать ставшие лишними импорты (`CURRENCY_SIGNS`, `group_digits`), если больше не используются.

- [ ] **Step 2: Проверка**

Run: `ruff check --select F apps/common apps/orders && DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_debt_payments.py -q`
Expected: All checks passed; тесты «Внести оплату» PASS.

---

### Task 4: Раскладка и проведение возврата

**Files:**
- Create: `backend/apps/orders/goods_returns.py`
- Test: `backend/apps/orders/tests/test_goods_returns.py` (дописать)

**Interfaces:**
- Consumes: Task 1 (модели, `sold_quantity`), Task 2 (`return_stock`), Task 3 (`money_text`), `debt.available_to_pay`, `debt.order_remaining`, `debt.oldest_debt_first`, `refunds.create_cash_refund`, `services.sync_payment_status`, `clients.services.lock_client_orders/lock_scoped_client`, `warehouse.services.resolve_warehouse`.
- Produces: `record_goods_return(client, user, *, currency, settlement, warehouse, lines, preview=False) -> dict` с ключами `currency, settlement, bags, amount, orders[{order_id, shipped_at, amount, lines[{label, bags, amount}]}]`, `return_id` (после проведения).

- [ ] **Step 1: Тесты раскладки и проведения**

Дописать в `test_goods_returns.py` (импорты — сверху файла: `from rest_framework.exceptions import PermissionDenied, ValidationError`, `from apps.eventlog.models import EventLog`, `from apps.orders.goods_returns import record_goods_return`, `from apps.orders.models import GoodsReturn, Payment, PaymentRefund`, `from apps.orders.services import record_staff_payment`, `from apps.warehouse.models import StockItem, StockMovement, Warehouse`):

```python
@pytest.fixture
def manager(user_with_perms, departments):
    return user_with_perms(
        "mill-manager", codes=["orders.edit", "payments.confirm", "payments.create"], department=departments[0],
    )


def _return(client, user, lines, *, settlement="debt", currency="KZT", preview=False, warehouse=None):
    return record_goods_return(
        client, user, currency=currency, settlement=settlement,
        warehouse=warehouse, lines=lines, preview=preview,
    )


def test_debt_return_goes_to_the_newest_shipment_first(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    now = timezone.now()
    old = _shipped(client, [(flour, 100, "3000")], shipped=now - timedelta(days=5))
    new = _shipped(client, [(flour, 20, "3200")], shipped=now - timedelta(days=1))

    plan = _return(client, manager, [{"product": flour.pk, "bags": 50}])

    assert [(row["order_id"], row["lines"][0]["bags"], row["amount"]) for row in plan["orders"]] == [
        (new.pk, 20, "64000.00"),
        (old.pk, 30, "90000.00"),
    ]
    assert (plan["bags"], plan["amount"], plan["settlement"]) == (50, "154000.00", "debt")
    assert OrderItem.objects.get(order=old).returned_quantity == 30
    assert OrderItem.objects.get(order=new).returned_quantity == 20
    old = Order.objects.prefetch_related("items", "payments").get(pk=old.pk)
    assert old.total_amount == Decimal("210000")  # 70 × 3000


def test_preview_writes_nothing(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "3000")])

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], preview=True)

    assert plan["bags"] == 4 and "return_id" not in plan
    assert not GoodsReturn.objects.exists()
    assert OrderItem.objects.get().returned_quantity == 0
    assert not StockMovement.objects.exists()


def test_debt_mode_takes_only_what_the_order_still_owes(manager, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    now = timezone.now()
    paid = _shipped(client, [(flour, 10, "1000")], shipped=now)  # новая, но оплачена
    record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    partly = _shipped(client, [(flour, 10, "1000")], shipped=now - timedelta(days=1))
    record_staff_payment(partly, Decimal("7500"), boss, method="cash")  # долг 2500 → 2 мешка

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 3}])

    assert error.value.detail["code"] == "goods_return_exceeds"
    assert "Максимум 2 мешка" in str(error.value.detail["detail"])
    plan = _return(client, manager, [{"product": flour.pk, "bags": 2}])
    assert [row["order_id"] for row in plan["orders"]] == [partly.pk]


def test_cash_mode_refunds_paid_orders_from_the_till(manager, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    paid = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    _shipped(client, [(flour, 10, "1000")], shipped=timezone.now() - timedelta(days=1))  # в долге — не для кассы

    plan = _return(client, manager, [{"product": flour.pk, "bags": 3}], settlement="cash")

    assert [row["order_id"] for row in plan["orders"]] == [paid.pk]
    refund = PaymentRefund.objects.get(payment=payment)
    assert (refund.amount, refund.method, refund.status) == (Decimal("3000.00"), "cash", "completed")
    assert refund.reason == f"Возврат товара №{plan['return_id']}"
    paid.refresh_from_db()
    assert paid.payment_status == "settled"  # 7 мешков = 7000, оплачено 10000 − 3000


def test_cash_mode_needs_payments_confirm(user_with_perms, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    editor = user_with_perms("editor", codes=["orders.edit"], department=departments[0])

    with pytest.raises(PermissionDenied):
        _return(client, editor, [{"product": flour.pk, "bags": 1}], settlement="cash")


def test_rows_of_the_same_flour_share_one_order_budget_and_add_up(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}, {"product": flour.pk, "bags": 5}])

    assert plan["bags"] == 9
    assert OrderItem.objects.get().returned_quantity == 9


def test_other_currency_trash_and_unpriced_items_are_not_used(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "10")], currency="USD")
    trashed = _shipped(client, [(flour, 10, "1000")])
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    _shipped(client, [(flour, 10, "0")])
    unpriced = _shipped(client, [(flour, 10, "1000")])
    OrderItem.objects.filter(order=unpriced).update(unit_price=None)

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 1}])

    assert error.value.detail["code"] == "goods_return_exceeds"


def test_return_puts_bags_on_the_chosen_warehouse_and_logs_each_order(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], warehouse=second.pk)

    assert StockItem.objects.get(product=flour, warehouse=second).bags == 4
    movement = StockMovement.objects.get(product=flour, warehouse=second)
    assert movement.reason == "client_return"
    event = EventLog.objects.get(event_type="goods_return", order=order)
    assert event.payload["goods_return_id"] == plan["return_id"]
    assert event.payload["bags"] == 4


@pytest.mark.parametrize("lines", [[], None, [{"product": 1, "bags": 0}], [{"product": 1, "bags": "3"}]])
def test_lines_must_be_positive_whole_bags(manager, departments, lines):
    with pytest.raises(ValidationError):
        _return(_client(departments[0]), manager, lines)


def test_unknown_settlement_and_currency_are_refused(manager, departments):
    client = _client(departments[0])
    with pytest.raises(ValidationError):
        _return(client, manager, [{"product": _flour().pk, "bags": 1}], settlement="gift")
    with pytest.raises(ValidationError):
        _return(client, manager, [{"product": _flour().pk, "bags": 1}], currency="EUR")
```

- [ ] **Step 2: Падают**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q`
Expected: FAIL — `ModuleNotFoundError: apps.orders.goods_returns`.

- [ ] **Step 3: Реализация**

Create `backend/apps/orders/goods_returns.py`:

```python
"""«Возврат» по клиенту: привезённые мешки раскладываются по его отгруженным заказам.

Позиция копит ``OrderItem.returned_quantity``: отгружено остаётся как было,
сумма и долг считаются за ``sold_quantity`` (``Order.total_amount``,
``querysets.item_value_sum``). Что с деньгами, выбирают в форме: ``debt``
уменьшает долг — только заказы со свободным остатком, переплаты нет;
``cash`` — касса отдаёт деньги: только оплаченные заказы, кассовые возвраты
их оплат. Порядок — от новой отгрузки к старой. Мешки приходят на выбранный
склад движением ``client_return``. Корзина не участвует, валюты не смешиваются.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.services import lock_client_orders, lock_scoped_client
from apps.common.money import CURRENCY_CODES, money_string, money_text
from apps.common.text import plural_ru
from apps.eventlog.services import log_event
from apps.warehouse.services import resolve_warehouse, return_stock

from .debt import DEBT_STATUS, available_to_pay, oldest_debt_first, order_remaining
from .models import GoodsReturn, GoodsReturnLine, Order, OrderItem
from .refunds import create_cash_refund
from .services import sync_payment_status

SETTLEMENT_TEXT = {"debt": "в счёт долга", "cash": "из кассы"}
_ZERO = Decimal("0")


def _bags_text(count: int) -> str:
    return f"{count} {plural_ru(count, 'мешок', 'мешка', 'мешков')}"


def _parsed_lines(raw) -> list[tuple[Product, int]]:
    """Строки формы ``[{product, bags}]`` → ``[(товар, мешков)]``; одинаковые товары складываются."""
    if not isinstance(raw, list) or not raw:
        raise ValidationError({
            "detail": "Укажите, какую муку и сколько мешков вернули",
            "code": "goods_return_empty",
        })
    bags: Counter = Counter()
    for row in raw:
        product_id = row.get("product") if isinstance(row, dict) else None
        count = row.get("bags") if isinstance(row, dict) else None
        whole = [value for value in (product_id, count) if isinstance(value, int) and not isinstance(value, bool)]
        if len(whole) != 2 or count <= 0:
            raise ValidationError({
                "detail": "Мешки — целое число больше нуля",
                "code": "goods_return_bad_line",
            })
        bags[product_id] += count
    products = Product.objects.in_bulk(list(bags))
    if len(products) != len(bags):
        raise ValidationError({"detail": "Товар не найден", "code": "product_not_found"})
    return [(products[pk], count) for pk, count in bags.items()]


def _candidates(client_pk: int, currency: str) -> list:
    """Отгруженные заказы клиента в валюте, от новой отгрузки к старой. Корзины нет: ``Order.objects``."""
    orders = (
        Order.objects.filter(client_id=client_pk, status=DEBT_STATUS, currency=currency)
        .select_related("shipment")
        .prefetch_related("payments", "items")
    )
    return sorted(orders, key=oldest_debt_first, reverse=True)


def _room(order, settlement: str) -> Decimal:
    """Сколько денег заказ примет возвратом: свободный долг или деньги к возврату у оплаченного."""
    if settlement == "debt":
        return available_to_pay(order)
    if order_remaining(order) > 0:
        return _ZERO
    return sum((payment.available_for_refund for payment in order.payments.all()), _ZERO)


def plan_goods_return(orders, lines, settlement: str) -> dict:
    """Разложить мешки по заказам — без записи.

    ``orders`` — :func:`_candidates` (с ``items`` и ``payments``), ``lines`` —
    ``[(товар, мешков)]``. Позиция отдаёт не больше ``sold_quantity``, заказ —
    не больше ``floor(запас / цена)`` мешков; запас денег заказа общий для всех
    строк. ``short`` — ``{товар: сколько поместилось}`` для не поместившихся.
    """
    room = {order.pk: _room(order, settlement) for order in orders}
    taken: dict[int, list] = defaultdict(list)
    short = {}
    for product, bags in lines:
        left = bags
        for order in orders:
            for item in order.items.all():
                if not left:
                    break
                if item.product_id != product.pk or not item.unit_price or item.unit_price <= 0:
                    continue
                fit = min(left, item.sold_quantity, int(room[order.pk] // item.unit_price))
                if fit <= 0:
                    continue
                taken[order.pk].append((item, fit))
                room[order.pk] -= fit * item.unit_price
                left -= fit
        if left:
            short[product] = bags - left
    slices = [
        {
            "order": order,
            "items": taken[order.pk],
            "amount": sum((item.unit_price * bags for item, bags in taken[order.pk]), _ZERO),
        }
        for order in orders
        if taken[order.pk]
    ]
    return {"slices": slices, "short": short}


def _refund_cash(order, amount: Decimal, user, reason: str) -> list[int]:
    """Касса отдаёт ``amount`` кассовыми возвратами оплат заказа — сначала новой оплаты."""
    refund_ids = []
    left = amount
    for payment in sorted(order.payments.all(), key=lambda payment: payment.pk, reverse=True):
        share = min(left, payment.available_for_refund)
        if share <= 0:
            continue
        refund_ids.append(create_cash_refund(payment, user, amount=share, reason=reason).pk)
        left -= share
        if not left:
            break
    return refund_ids


def _record(client, user, plan: dict, lines, *, currency: str, settlement: str, warehouse) -> GoodsReturn:
    goods_return = GoodsReturn.objects.create(
        client=client, currency=currency, settlement=settlement, warehouse=warehouse, created_by=user,
    )
    reason = f"Возврат товара №{goods_return.pk}"
    for share in plan["slices"]:
        order = share["order"]
        for item, bags in share["items"]:
            GoodsReturnLine.objects.create(
                goods_return=goods_return, order_item=item, bags=bags,
                unit_price=item.unit_price, amount=item.unit_price * bags,
            )
            OrderItem.objects.filter(pk=item.pk).update(returned_quantity=F("returned_quantity") + bags)
        refund_ids = _refund_cash(order, share["amount"], user, reason) if settlement == "cash" else []
        sync_payment_status(Order.objects.get(pk=order.pk))
        bags = sum(count for _item, count in share["items"])
        log_event(
            "goods_return",
            f"{reason}: {_bags_text(bags)} · {money_text(share['amount'], currency)} {SETTLEMENT_TEXT[settlement]}",
            user=user,
            order=order,
            payload={
                "goods_return_id": goods_return.pk,
                "settlement": settlement,
                "bags": bags,
                "amount": money_string(share["amount"]),
                "lines": [
                    {"order_item_id": item.pk, "product_id": item.product_id, "bags": count,
                     "amount": money_string(item.unit_price * count)}
                    for item, count in share["items"]
                ],
                "refund_ids": refund_ids,
            },
        )
    for product, bags in lines:
        return_stock(product, bags, user, warehouse, note=f"{reason}, клиент «{client.display_name}»")
    return goods_return


def _payload(plan: dict, *, currency: str, settlement: str) -> dict:
    """Раскладка в ответе API: деньги строками, мешки числами, подписи без цвета."""
    return {
        "currency": currency,
        "settlement": settlement,
        "bags": sum(bags for share in plan["slices"] for _item, bags in share["items"]),
        "amount": money_string(sum((share["amount"] for share in plan["slices"]), _ZERO)),
        "orders": [
            {
                "order_id": share["order"].pk,
                "shipped_at": timezone.localtime(share["order"].sale_at).isoformat(),
                "amount": money_string(share["amount"]),
                "lines": [
                    {"label": item.product_plain_label, "bags": bags, "amount": money_string(item.unit_price * bags)}
                    for item, bags in share["items"]
                ],
            }
            for share in plan["slices"]
        ],
    }


@transaction.atomic
def record_goods_return(
    client, user, *, currency, settlement, warehouse, lines, preview: bool = False,
) -> dict:
    """«Возврат»: разложить мешки клиента по отгруженным заказам и провести.

    ``preview`` — только раскладка, без блокировок и записи. Проведение — под
    блокировкой заказов и клиента в области отдела сотрудника (порядок как у
    «Внести оплату»), раскладка пересчитывается под ней. Больше, чем
    помещается, не принимаем: «Максимум N мешков …».
    """
    if currency not in CURRENCY_CODES:
        raise ValidationError({"detail": "Неизвестная валюта", "code": "bad_currency"})
    if settlement not in SETTLEMENT_TEXT:
        raise ValidationError({"detail": "Выберите: в счёт долга или из кассы", "code": "bad_settlement"})
    if settlement == "cash" and not user.has_perm_code("payments.confirm"):
        raise PermissionDenied("Отдать деньги из кассы может тот, кто делает возврат оплаты")
    parsed = _parsed_lines(lines)
    warehouse = resolve_warehouse(warehouse or None)
    if not preview:
        lock_client_orders(client.pk)
        client = lock_scoped_client(client.pk, user)
    plan = plan_goods_return(_candidates(client.pk, currency), parsed, settlement)
    if plan["short"]:
        product, fits = next(iter(plan["short"].items()))
        raise ValidationError({
            "detail": f"Максимум {_bags_text(fits)} «{product.plain_label}» {SETTLEMENT_TEXT[settlement]}",
            "code": "goods_return_exceeds",
            "product": product.pk,
            "max_bags": fits,
        })
    payload = _payload(plan, currency=currency, settlement=settlement)
    if not preview:
        payload["return_id"] = _record(
            client, user, plan, parsed, currency=currency, settlement=settlement, warehouse=warehouse,
        ).pk
    return payload
```

Замечание: `str(error.value.detail["detail"])` в тесте `test_debt_mode_takes_only_what_the_order_still_owes` ждёт «Максимум 2 мешка» — `_bags_text(2)` даёт «2 мешка».

- [ ] **Step 4: Проходят**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q`
Expected: PASS. Если `user_with_perms` не принимает `department=` — посмотри сигнатуру в `apps/conftest.py:154` и приведи фикстуру к ней (так же, как `cashier` в `test_debt_payments.py`).

---

### Task 5: Ручка `POST /api/clients/{id}/goods-return/`

**Files:**
- Modify: `backend/apps/clients/views.py` (`required_perms` ~стр. 167; action рядом с `debt_payment` ~стр. 605)
- Test: `backend/apps/orders/tests/test_goods_returns.py` (дописать)

**Interfaces:**
- Consumes: `record_goods_return` (Task 4).
- Produces: HTTP `POST /api/clients/{id}/goods-return/`, тело `{currency, settlement, warehouse, lines, preview}` → раскладка (Task 4).

- [ ] **Step 1: Тест API и прав**

```python
def test_api_previews_and_records_a_return(auth_client, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    body = {"currency": "KZT", "settlement": "debt", "warehouse": None,
            "lines": [{"product": flour.pk, "bags": 3}]}
    api = auth_client(manager)

    preview = api.post(f"/api/clients/{client.pk}/goods-return/", {**body, "preview": True}, format="json")
    done = api.post(f"/api/clients/{client.pk}/goods-return/", {**body, "preview": False}, format="json")

    assert preview.status_code == 200, preview.data
    assert preview.data["bags"] == 3 and "return_id" not in preview.data
    assert done.status_code == 200, done.data
    assert done.data["return_id"] == GoodsReturn.objects.get().pk


def test_api_needs_orders_edit_and_boolean_preview(auth_client, user_with_perms, manager, departments):
    client = _client(departments[0])
    viewer = user_with_perms("viewer", codes=["clients.view"], department=departments[0])
    body = {"currency": "KZT", "settlement": "debt", "lines": [{"product": _flour().pk, "bags": 1}]}

    assert auth_client(viewer).post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 403
    response = auth_client(manager).post(
        f"/api/clients/{client.pk}/goods-return/", {**body, "preview": "true"}, format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "bad_preview"
```

- [ ] **Step 2: Падает**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q -k api`
Expected: FAIL — 404.

- [ ] **Step 3: Реализация**

В `required_perms` `ClientViewSet` после `"debt_payment": "payments.create",`:

```python
        # «Возврат» товара по клиенту (orders/goods_returns.py); «из кассы» сервис
        # дополнительно проверяет payments.confirm.
        "goods_return": "orders.edit",
```

Импорт: `from apps.orders.goods_returns import record_goods_return`. Action после `debt_payment`:

```python
    @action(detail=True, methods=["post"], url_path="goods-return")
    def goods_return(self, request, pk=None):
        """«Возврат»: мешки клиента раскладываются по его отгруженным заказам.

        Раскладку, проверки и проведение делает
        ``apps.orders.goods_returns.record_goods_return``; ``preview`` — только
        раскладка. Здесь проверяется только форма признака предпросмотра.
        """
        client = self.get_object()
        preview = request.data.get("preview", False)
        if not isinstance(preview, bool):
            raise ValidationError({
                "detail": "Признак предпросмотра должен быть true или false",
                "code": "bad_preview",
            })
        return Response(record_goods_return(
            client,
            request.user,
            currency=request.data.get("currency"),
            settlement=request.data.get("settlement"),
            warehouse=request.data.get("warehouse"),
            lines=request.data.get("lines"),
            preview=preview,
        ))
```

Если клиентский роутер регистрирует actions автоматически (как `debt-payment`), urls не трогать; проверь `rg -n "debt-payment" backend/apps/clients/urls.py` — если там явный path, добавь такой же для `goods-return`.

- [ ] **Step 4: Проходит**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q`
Expected: PASS. Если тест каталога прав/эндпоинтов (`apps/sys_permissions/tests/test_catalog.py`) перечисляет действия — прогнать его и добавить `goods_return`, если требуется.

---

### Task 6: Заказ с возвратом — откат отгрузки и правка состава отказывают

**Files:**
- Modify: `backend/apps/orders/services.py:1389` (`replace_items`, после проверки `items_empty`)
- Modify: `backend/apps/shipments/services.py:438` (`rollback_shipment`, после проверки `invalid_status`)
- Test: `backend/apps/orders/tests/test_goods_returns.py` (дописать)

**Interfaces:**
- Produces: 400 `{"code": "order_has_returns"}`.

- [ ] **Step 1: Тест**

```python
def test_order_with_a_return_cannot_be_rolled_back_or_recomposed(manager, departments, boss):
    from apps.orders.services import replace_items
    from apps.shipments.services import ROLLBACK_TARGET_STATUSES, rollback_shipment

    ROLLBACK_TARGET = sorted(ROLLBACK_TARGET_STATUSES)[0]  # любой допустимый: отказ раньше проверки денег

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 2}])

    with pytest.raises(ValidationError) as rollback:
        rollback_shipment(order, boss, target_status=ROLLBACK_TARGET, reason="ошибка отгрузки")
    with pytest.raises(ValidationError) as edit:
        replace_items(order, [{"product": flour, "quantity": 5}], {flour.pk: Decimal("1000")}, boss,
                      edit_reason="исправление количества")

    assert rollback.value.detail["code"] == "order_has_returns"
    assert edit.value.detail["code"] == "order_has_returns"
```

(Формат `items_data` у `replace_items` сверь с существующими тестами: `rg -n "replace_items\(" backend/apps/orders/tests | head -3` — передай так же.)

- [ ] **Step 2: Падает**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q -k rolled_back`
Expected: FAIL (нет отказа).

- [ ] **Step 3: Реализация**

Общая проверка — в `backend/apps/orders/goods_returns.py`:

```python
def assert_no_returns(order) -> None:
    """Заказ с возвратом не откатывают и не перекраивают: строки возврата держат его позиции."""
    if OrderItem.objects.filter(order=order, returned_quantity__gt=0).exists():
        raise ValidationError({
            "detail": "У заказа есть возврат товара — откат и правка состава недоступны",
            "code": "order_has_returns",
        })
```

В `replace_items` после блока `items_empty` — `assert_no_returns(order)` (импорт внутри функции `from .goods_returns import assert_no_returns`, чтобы не было цикла: `goods_returns` импортирует `services`). В `rollback_shipment` после проверки `invalid_status` — `from apps.orders.goods_returns import assert_no_returns` и вызов `assert_no_returns(order)`.

- [ ] **Step 4: Проходит + соседние тесты**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders apps/shipments -q`
Expected: PASS.

---

### Task 7: Возврат в API заказа и в выписке

**Files:**
- Modify: `backend/apps/orders/serializers.py:41-63` (`OrderItemSerializer`)
- Modify: `backend/apps/clients/reports/statements/presentation.py:70-76`, `xlsx.py:455-470`, `pdf.py:410-424`
- Test: `backend/apps/orders/tests/test_goods_returns.py` (дописать)

- [ ] **Step 1: Тест**

```python
def test_order_api_and_statement_show_the_return(auth_client, manager, departments, boss):
    from apps.clients.reports.statements.presentation import operation_display

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 3}])

    item = auth_client(boss).get(f"/api/orders/{order.pk}/").data["items"][0]
    assert (item["quantity"], item["returned_quantity"]) == (10, 3)

    class Operation:  # строка ленты выписки: продажа этого заказа
        kind = "sale"

    Operation.order = Order.objects.prefetch_related("items").get(pk=order.pk)
    assert operation_display(Operation).description.endswith("× 10 (возврат 3)")
```

(Поле описания `OperationDisplay` сверь: `rg -n "class OperationDisplay" -A6 backend/apps/clients/reports/statements/presentation.py` — используй его имя вместо `description`, если другое.)

- [ ] **Step 2: Падает**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py -q -k statement`
Expected: FAIL — нет `returned_quantity`.

- [ ] **Step 3: Реализация**

`OrderItemSerializer`: `returned_quantity = serializers.IntegerField(read_only=True)` и `"returned_quantity"` в `fields` после `"quantity"`.

`presentation.py` — подпись позиции:

```python
            ", ".join(
                f"{item.product_label} × {item.quantity}"
                + (f" (возврат {item.returned_quantity})" if item.returned_quantity else "")
                for item in order.items.all()
            ),
```

`xlsx.py` `_items_sheet` — после колонки «Мешков»:

```python
        Column("Возврат", 12, lambda row: row[1].returned_quantity, "number"),
```

и «Сумма» — `lambda row: row[1].sold_quantity * (row[1].unit_price or 0)`.

`pdf.py` `_items` — после «Мешков»: `Column("Возврат", (20, 18), lambda row: row[1].returned_quantity, "number"),` и «Сумма» — `row[1].sold_quantity * (row[1].unit_price or 0)`. Если ширины PDF-таблицы считаются в сумме (A4), уменьши ширину «Товар» на ширину новой колонки (`(70, 56)`), чтобы таблица не вылезла.

- [ ] **Step 4: Проходит + выписки**

Run: `DB_NAME=asyl_goods_return .venv/bin/pytest apps/orders/tests/test_goods_returns.py apps/clients -q`
Expected: PASS (если тест выписки сверяет заголовки/ширины колонок «Позиции» — обнови ожидания на новую колонку «Возврат»).

---

### Task 8: Фронт — общий выбор клиента из `OrderForm`

**Files:**
- Create: `frontend/src/components/orders/order-form-parts.tsx`
- Modify: `frontend/src/components/order-form.tsx` (стр. 41-122 — типы и `SectionTitle`; стр. 257-263 — `filteredClients`; стр. 500-597 — разметка выбора клиента)

**Interfaces:**
- Produces: `SectionTitle({step, title, caption?, aside?})`, `ClientPicker({clients, value, search, onSearch, listOpen, onOpenList, onChoose, badge?, canChange?})`, типы `OrderClientOption`, `OrderProductOption`, `OrderFormOptions`, константа `EMPTY_FORM_OPTIONS`.

- [ ] **Step 1: Вынести**

Create `frontend/src/components/orders/order-form-parts.tsx`:

```tsx
"use client";
import { Check, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Client, Department, Product, Store, Warehouse } from "@/lib/types";
import { cn } from "@/lib/utils";

export type OrderClientOption = Pick<Client, "id" | "name" | "company_name" | "phone" | "currency"> & {
  /** Страна клиента — страна номера машины по умолчанию. */
  country?: string;
  department_code?: string;
  department_name?: string;
};
export type OrderProductOption = Pick<Product, "id" | "label" | "available_bags"> & {
  /** Остаток мешков по складам: ключ — id склада; склада без записи у товара нет. */
  stock_by_warehouse: Record<string, number>;
};
export type OrderStoreOption = Pick<Store, "id" | "client" | "name" | "address">;
export type OrderDepartmentOption = Pick<Department, "id" | "code" | "name" | "color" | "is_default">;

/** GET /orders/form-options/ — справочники «Нового заказа» и «Возврата». */
export interface OrderFormOptions {
  clients: OrderClientOption[];
  products: OrderProductOption[];
  stores: OrderStoreOption[];
  departments: OrderDepartmentOption[];
  warehouses?: Warehouse[];
}

export const EMPTY_FORM_OPTIONS: OrderFormOptions = {
  clients: [],
  products: [],
  stores: [],
  departments: [],
  warehouses: [],
};

export function SectionTitle({
  step,
  title,
  caption,
  aside,
}: {
  step: number;
  title: string;
  caption?: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
          {step}
        </span>
        <div>
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          {caption && <p className="text-xs text-slate-500">{caption}</p>}
        </div>
      </div>
      {aside}
    </div>
  );
}

/**
 * Шаг «Клиент»: выбранный клиент карточкой с «Изменить» или поиск по списку.
 * Один для «Нового заказа» и «Возврата».
 */
export function ClientPicker({
  clients,
  value,
  search,
  onSearch,
  listOpen,
  onOpenList,
  onChoose,
  badge,
  canChange = true,
}: {
  clients: OrderClientOption[];
  value: string;
  search: string;
  onSearch: (value: string) => void;
  /** Поиск со списком вместо карточки выбранного. */
  listOpen: boolean;
  onOpenList: () => void;
  onChoose: (client: OrderClientOption) => void;
  /** Справа в карточке выбранного — например, отдел. */
  badge?: React.ReactNode;
  canChange?: boolean;
}) {
  const selected = clients.find((item) => String(item.id) === value);
  const needle = search.trim().toLocaleLowerCase("ru");
  const filtered = needle
    ? clients.filter((item) =>
        `${item.name} ${item.company_name || ""} ${item.phone || ""}`.toLocaleLowerCase("ru").includes(needle),
      )
    : clients;
  return (
    <>
      {selected && !listOpen && (
        // ← сюда без изменений переносится карточка выбранного клиента из order-form.tsx (стр. 500-527):
        //    `selectedClient` → `selected`, бейдж отдела → `{badge}`, условие кнопки «Изменить»
        //    `!editing` → `canChange`, её onClick → `onOpenList`.
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-bold text-white">
            {selected.name.slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-slate-900">{selected.name}</div>
            <div className="truncate text-xs text-slate-500">
              {[selected.company_name, selected.phone].filter(Boolean).join(" · ") || "Без дополнительных данных"}
            </div>
          </div>
          {badge}
          {canChange && (
            <Button type="button" variant="outline" size="sm" onClick={onOpenList}>
              Изменить
            </Button>
          )}
        </div>
      )}
      {listOpen && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="relative border-b border-slate-100">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(event) => onSearch(event.target.value)}
              placeholder="Имя, компания или телефон…"
              className="h-12 w-full bg-transparent pl-10 pr-4 text-sm outline-none placeholder:text-slate-400"
              autoFocus={!value}
              aria-label="Поиск клиента"
            />
          </div>
          <div className="max-h-64 overflow-y-auto p-1.5">
            {filtered.map((item) => {
              const isSelected = String(item.id) === value;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onChoose(item)}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-3 rounded-lg px-3 py-2 text-left transition",
                    isSelected ? "bg-slate-100" : "hover:bg-slate-50",
                  )}
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-600">
                    {item.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-900">{item.name}</span>
                    <span className="block truncate text-xs text-slate-500">
                      {[item.company_name, item.phone].filter(Boolean).join(" · ") || "—"}
                    </span>
                  </span>
                  {item.department_name && (
                    <span className="hidden shrink-0 text-[11px] text-slate-400 sm:block">{item.department_name}</span>
                  )}
                  {isSelected && <Check className="size-4 shrink-0 text-slate-900" />}
                </button>
              );
            })}
            {!filtered.length && (
              <div className="flex min-h-24 flex-col items-center justify-center text-center text-slate-400">
                <span className="text-sm font-medium">Ничего не найдено</span>
                <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
```

(Комментарий-стрелку «← сюда без изменений…» в итоговый код не переносить — он для исполнителя: сверь, что разметка совпадает с текущей в `order-form.tsx`, и при расхождениях возьми текущую.)

В `order-form.tsx`:
- удалить локальные `OrderClientOption`, `OrderProductOption`, `OrderStoreOption`, `OrderDepartmentOption`, `OrderFormOptions`, `EMPTY_FORM_OPTIONS`, `SectionTitle`; импортировать их из `@/components/orders/order-form-parts`;
- удалить `normalizedSearch`/`filteredClients` (если `normalizedSearch` больше нигде не нужен);
- разметку стр. 500-597 (карточка + список) заменить на:

```tsx
              <ClientPicker
                clients={clients}
                value={client}
                search={clientSearch}
                onSearch={setClientSearch}
                listOpen={clientPickerVisible}
                onOpenList={() => setClientPickerOpen(true)}
                onChoose={chooseClient}
                canChange={!editing}
                badge={
                  assignedDepartment && (
                    <span className="hidden items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 sm:inline-flex">
                      <DepartmentDot color={assignedDepartment.color} className="size-2" />
                      {assignedDepartment.name}
                      <span className="font-normal text-slate-400">
                        · {clientDepartment ? "отдел клиента" : "ваш отдел"}
                      </span>
                    </span>
                  )
                }
              />
```

- убрать из импортов `Check`, `Search`, если больше не используются.

- [ ] **Step 2: Регрессия формы заказа**

Run (из `frontend/`): `npx tsc --noEmit -p . && npx eslint src/components/order-form.tsx src/components/orders/order-form-parts.tsx && npx vitest run src/components src/app/orders`
Expected: всё зелёное — поведение «Нового заказа» не изменилось.

---

### Task 9: Фронт — окно «Возврат», кнопка на /orders, возврат в карточке заказа

**Files:**
- Create: `frontend/src/components/orders/goods-return-modal.tsx`, `frontend/src/components/orders/goods-return-modal.test.tsx`
- Modify: `frontend/src/app/orders/page.tsx` (импорты; права ~стр. 580; кнопки ~стр. 688-716; модалки ~стр. 995)
- Modify: `frontend/src/app/orders/[id]/page.tsx:349-370`
- Modify: `frontend/src/lib/types.ts:284-292` (`OrderItem`)

**Interfaces:**
- Consumes: Task 5 (API), Task 8 (`ClientPicker`, `SectionTitle`, `OrderFormOptions`, `EMPTY_FORM_OPTIONS`).
- Produces: `GoodsReturnModal({open, onClose, onDone})`.

- [ ] **Step 1: Тест окна**

Create `frontend/src/components/orders/goods-return-modal.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GoodsReturnModal } from "./goods-return-modal";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), permissions: ["orders.edit"] as string[] }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (error: Error) => error.message,
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const OPTIONS = {
  clients: [{ id: 7, name: "Нуржан Сарыагаш", company_name: "", phone: "+7 (700) 000-00-00", currency: "KZT" }],
  products: [{ id: 3, label: "Первый сорт DIKHAN 50кг", available_bags: 0, stock_by_warehouse: {} }],
  stores: [],
  departments: [],
  warehouses: [{ id: 1, code: "main", name: "Мельница", address: "", is_active: true, is_default: true }],
};
const PLAN = {
  currency: "KZT",
  settlement: "debt",
  bags: 50,
  amount: "154000.00",
  orders: [
    { order_id: 903, shipped_at: "2026-10-04T12:00:00+05:00", amount: "64000.00",
      lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 20, amount: "64000.00" }] },
    { order_id: 891, shipped_at: "2026-09-23T12:00:00+05:00", amount: "90000.00",
      lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 30, amount: "90000.00" }] },
  ],
};

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Нуржан Сарыагаш/ }));
  await user.selectOptions(screen.getByLabelText("Мука 1"), "3");
  await user.type(screen.getByLabelText("Мешков 1"), "50");
}

describe("GoodsReturnModal", () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue({ data: OPTIONS });
    mocks.post.mockReset();
    mocks.permissions = ["orders.edit"];
  });

  it("сначала раскладка с сервера по заказам, потом подтверждение", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    mocks.post
      .mockResolvedValueOnce({ data: PLAN })
      .mockResolvedValueOnce({ data: { ...PLAN, return_id: 12 } });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={onDone} />);

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    const plan = await screen.findByRole("region", { name: "Раскладка возврата" });
    expect(within(plan).getByText(/#903/)).toBeInTheDocument();
    expect(plan).toHaveTextContent("20 мешков");
    expect(plan).toHaveTextContent("Итого 50 мешков");
    expect(mocks.post).toHaveBeenLastCalledWith("/clients/7/goods-return/", {
      currency: "KZT",
      settlement: "debt",
      warehouse: 1,
      lines: [{ product: 3, bags: 50 }],
      preview: true,
    });

    await user.click(screen.getByRole("button", { name: "Подтвердить возврат" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenLastCalledWith("/clients/7/goods-return/", expect.objectContaining({ preview: false }));
  });

  it("«Деньги из кассы» — только с правом возврата оплаты", async () => {
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);
    expect(await screen.findByRole("radio", { name: "Деньги из кассы" })).toBeDisabled();
  });

  it("ошибку сервера показывает в окне, раскладку сбрасывает правка", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValueOnce(new Error("Максимум 20 мешков «Первый сорт DIKHAN 50кг» в счёт долга"));
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(await screen.findByText(/Максимум 20 мешков/)).toBeInTheDocument();
  });
});
```

(Роль опций `Segmented` сверь: `rg -n "role=" frontend/src/components/ui/segmented.tsx`. Если это `button` с `aria-pressed`, а не `radio`, поправь второй тест на `getByRole("button", { name: "Деньги из кассы" })`.)

- [ ] **Step 2: Падает**

Run: `npx vitest run src/components/orders/goods-return-modal.test.tsx`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Окно**

Create `frontend/src/components/orders/goods-return-modal.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  ClientPicker,
  EMPTY_FORM_OPTIONS,
  SectionTitle,
  type OrderClientOption,
  type OrderFormOptions,
} from "@/components/orders/order-form-parts";
import { Button } from "@/components/ui/button";
import { DataGate, FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { showSuccess } from "@/lib/toast";
import { useApi } from "@/lib/use-api";
import { bagsLabel, formatCurrency, formatIsoDayMonth } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type Settlement = "debt" | "cash";
type Row = { id: number; product: string; bags: string };

/** Раскладка возврата с сервера: заказ → мешки и сумма (orders/goods_returns.py). */
interface GoodsReturnPlan {
  currency: "KZT" | "USD";
  settlement: Settlement;
  bags: number;
  amount: string;
  orders: {
    order_id: number;
    shipped_at: string;
    amount: string;
    lines: { label: string; bags: number; amount: string }[];
  }[];
  return_id?: number;
}

/** «Возврат»: клиент привёз мешки — сервер раскладывает их по его отгруженным заказам. */
export function GoodsReturnModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Работа · Возврат"
      title="Возврат товара"
      description="Клиент привёз мешки обратно — система сама разложит их по его отгруженным заказам."
      className="max-w-5xl"
      mobileFullscreen
      dismissible={false}
    >
      {open && <GoodsReturnForm onCancel={onClose} onDone={onDone} />}
    </Modal>
  );
}

function GoodsReturnForm({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const { me } = useAuth();
  const { data, loading, error: loadError, reload } = useApi<OrderFormOptions>("/orders/form-options/");
  const { clients, products, warehouses = [] } = data ?? EMPTY_FORM_OPTIONS;
  const [client, setClient] = useState("");
  const [search, setSearch] = useState("");
  const [listOpen, setListOpen] = useState(true);
  const [currency, setCurrency] = useState<"KZT" | "USD">("KZT");
  const [warehouse, setWarehouse] = useState("");
  const [settlement, setSettlement] = useState<Settlement>("debt");
  const nextRowId = useRef(1);
  const [rows, setRows] = useState<Row[]>([{ id: 0, product: "", bags: "" }]);
  const [plan, setPlan] = useState<GoodsReturnPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const canCash = can(me, "payments.confirm");

  useEffect(() => {
    if (warehouse || warehouses.length === 0) return;
    const initial = warehouses.find((item) => item.is_default) ?? warehouses[0];
    setWarehouse(String(initial.id));
  }, [warehouse, warehouses]);

  // Любая правка формы делает раскладку устаревшей: подтверждать можно только свежую.
  function edited() {
    setPlan(null);
    setError("");
  }

  function chooseClient(item: OrderClientOption) {
    setClient(String(item.id));
    setCurrency(item.currency);
    setSearch("");
    setListOpen(false);
    edited();
  }

  const lines = rows
    .filter((row) => row.product && Number(row.bags) > 0)
    .map((row) => ({ product: Number(row.product), bags: Number(row.bags) }));
  const ready = Boolean(client) && lines.length > 0 && lines.length === rows.length;

  async function submit(preview: boolean) {
    setBusy(true);
    setError("");
    try {
      const { data: result } = await api.post<GoodsReturnPlan>(`/clients/${client}/goods-return/`, {
        currency,
        settlement,
        warehouse: warehouse ? Number(warehouse) : null,
        lines,
        preview,
      });
      if (preview) {
        setPlan(result);
        return;
      }
      showSuccess(`Возврат №${result.return_id}: ${bagsLabel(result.bags)}`);
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <DataGate loading={loading} error={loadError || undefined} onRetry={reload} />;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
      <div className="min-w-0 space-y-8">
        <section className="space-y-4">
          <SectionTitle step={1} title="Клиент" caption="Кто привёз мешки." />
          <ClientPicker
            clients={clients}
            value={client}
            search={search}
            onSearch={setSearch}
            listOpen={listOpen || !client}
            onOpenList={() => setListOpen(true)}
            onChoose={chooseClient}
          />
        </section>

        <section className="space-y-4">
          <SectionTitle step={2} title="Что вернули" caption="Мука и сколько мешков — цену берём из заказов." />
          {rows.map((row, index) => (
            <div key={row.id} className="grid grid-cols-[minmax(0,1fr)_110px_auto] items-end gap-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`return-product-${row.id}`}>Мука {index + 1}</Label>
                <Select
                  id={`return-product-${row.id}`}
                  value={row.product}
                  onChange={(event) => {
                    const product = event.target.value;
                    setRows((current) => current.map((item) => (item.id === row.id ? { ...item, product } : item)));
                    edited();
                  }}
                  className="h-10 rounded-lg bg-white"
                >
                  <option value="">Выберите муку</option>
                  {products.map((product) => (
                    <option key={product.id} value={product.id}>
                      {product.label}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`return-bags-${row.id}`}>Мешков {index + 1}</Label>
                <Input
                  id={`return-bags-${row.id}`}
                  inputMode="numeric"
                  value={row.bags}
                  onChange={(event) => {
                    const bags = event.target.value.replace(/\D/g, "");
                    setRows((current) => current.map((item) => (item.id === row.id ? { ...item, bags } : item)));
                    edited();
                  }}
                  className="h-10 text-right tabular-nums"
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`Убрать строку ${index + 1}`}
                disabled={rows.length === 1}
                onClick={() => {
                  setRows((current) => current.filter((item) => item.id !== row.id));
                  edited();
                }}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setRows((current) => [...current, { id: nextRowId.current++, product: "", bags: "" }]);
              edited();
            }}
          >
            <Plus className="size-4" /> Ещё мука
          </Button>
        </section>

        {plan && (
          <section aria-label="Раскладка возврата" className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            {plan.orders.map((order) => (
              <div key={order.order_id} className="flex items-start justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <div className="font-semibold">
                    #{order.order_id} · отгружен {formatIsoDayMonth(order.shipped_at.slice(0, 10))}
                  </div>
                  {order.lines.map((line, index) => (
                    <div key={index} className="text-slate-600">
                      {line.label} — {bagsLabel(line.bags)}
                    </div>
                  ))}
                </div>
                <div className="shrink-0 font-semibold tabular-nums">{formatCurrency(order.amount, plan.currency)}</div>
              </div>
            ))}
            <div className="flex justify-between border-t border-slate-200 pt-3 font-bold">
              <span>Итого {bagsLabel(plan.bags)}</span>
              <span className="tabular-nums">
                {formatCurrency(plan.amount, plan.currency)} {plan.settlement === "debt" ? "в счёт долга" : "из кассы"}
              </span>
            </div>
          </section>
        )}

        <FormError message={error} />
      </div>

      <aside className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="grid gap-1.5">
          <Label>Валюта</Label>
          <Segmented
            ariaLabel="Валюта"
            value={currency}
            options={[
              { value: "KZT", label: "₸ Тенге" },
              { value: "USD", label: "$ Доллары" },
            ]}
            onChange={(value) => {
              setCurrency(value);
              edited();
            }}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="return-warehouse">Склад, куда кладём мешки</Label>
          <Select
            id="return-warehouse"
            value={warehouse}
            onChange={(event) => {
              setWarehouse(event.target.value);
              edited();
            }}
            className="h-10 rounded-lg bg-white"
          >
            {warehouses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.is_default ? " · основной" : ""}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label>Деньги</Label>
          <Segmented
            ariaLabel="Деньги"
            value={settlement}
            options={[
              { value: "debt", label: "Уменьшить долг" },
              { value: "cash", label: "Деньги из кассы", disabled: !canCash },
            ]}
            onChange={(value) => {
              setSettlement(value);
              edited();
            }}
          />
        </div>
        <div className="flex flex-col gap-2 pt-2">
          {plan ? (
            <Button disabled={busy} onClick={() => void submit(false)}>
              Подтвердить возврат
            </Button>
          ) : (
            <Button disabled={busy || !ready} onClick={() => void submit(true)}>
              Проверить
            </Button>
          )}
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </aside>
    </div>
  );
}
```

Сверь API `Segmented` (`frontend/src/components/ui/segmented.tsx`): если у опции нет `disabled`, добавь в `SegmentedOption` необязательное `disabled?: boolean` и передай его в `disabled` кнопки опции — это единственное изменение `Segmented`. Если у `formatIsoDayMonth` другая сигнатура (`rg -n "export function formatIsoDayMonth" -A6 frontend/src/lib/utils.ts`), подставь подходящий формат даты из `lib/utils`.

- [ ] **Step 4: Кнопка на /orders**

`frontend/src/app/orders/page.tsx`:
- импорт: `const GoodsReturnModal = dynamic(() => import("@/components/orders/goods-return-modal").then((m) => m.GoodsReturnModal));` рядом с `const OrderForm = dynamic(...)`; `Undo2` — в импорт `lucide-react`;
- после `const canCreate = …`: `const canReturn = can(me, "orders.edit");` и `const [returnOpen, setReturnOpen] = useState(false);`;
- условие `actions`: `canCreate || canReturn || canManageDepartments || canExport`;
- перед кнопкой «Новый заказ»:

```tsx
            {canReturn && (
              <Button size="sm" variant="outline" aria-label="Возврат" onClick={() => setReturnOpen(true)}>
                <Undo2 className="size-4" /> <span className="hidden sm:inline">Возврат</span>
              </Button>
            )}
```

- после `<Modal …>` «Нового заказа»:

```tsx
      <GoodsReturnModal
        open={returnOpen}
        onClose={() => setReturnOpen(false)}
        onDone={() => {
          setReturnOpen(false);
          reload();
        }}
      />
```

- [ ] **Step 5: Возврат в карточке заказа и тип**

`frontend/src/lib/types.ts`, `interface OrderItem` — после `quantity: number;`:

```ts
  /** Сколько мешков клиент вернул («Возврат»); деньги — за quantity − returned_quantity. */
  returned_quantity?: number;
```

`frontend/src/app/orders/[id]/page.tsx` (стр. ~349-366): `const sum = price * (Number(it.quantity) - (it.returned_quantity ?? 0));` и ячейка количества:

```tsx
                          <TD className="text-right tabular-nums">
                            {it.quantity}
                            {it.returned_quantity ? (
                              <span className="block text-xs font-medium text-[var(--destructive)]">
                                возврат {it.returned_quantity}
                              </span>
                            ) : null}
                          </TD>
```

- [ ] **Step 6: Проходит**

Run: `npx vitest run src/components/orders src/app/orders && npx tsc --noEmit -p .`
Expected: PASS.

---

### Task 10: Полная проверка и прокликивание

- [ ] **Step 1: Бэкенд целиком**

Run (из `backend/`): `ruff check --select F apps && .venv/bin/python manage.py makemigrations --check --dry-run && DB_NAME=asyl_goods_return .venv/bin/pytest -q`
Expected: All checks passed; No changes detected; все тесты PASS.

- [ ] **Step 2: Фронт целиком**

Run (из `frontend/`): `npm run check && npx knip --no-progress && npm run build`
Expected: зелёное; knip — без новых находок (старый `WaybillSigner` был и раньше).

- [ ] **Step 3: Стенд и скрины**

Одноразовая база (см. память `local-testing-setup`): `CREATE DATABASE asyl_lbl` → `migrate` → сид (superuser, клиент, 2–3 отгруженных заказа с одной мукой, один оплаченный) → `backend-lbl-8010` + `frontend-proxy-8010` → /orders → «Возврат»: долг (раскладка от новой к старой, «Максимум N»), касса (оплаченный заказ, кассовый возврат), карточка заказа «возврат N». Скрины владельцу. Убрать базу, конфиг и пароль.
