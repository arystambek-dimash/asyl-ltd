# POS в кассе — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кассир на телефоне принимает оплату долга как в Kaspi POS: клиент → заказ → сумма на клавиатуре → Kaspi QR на экране → «Оплачено»; табы «Оплата · Удаленно · История»; кнопка POS внизу по центру.

**Architecture:** Бэкенд получает кассовый канал `kaspi` + `channel: "qr"` (оплата создаётся кассовым `add_payment`, QR — существующим `create_invoice(channel="qr")`) и GET статуса одной оплаты. Фронт: новый мобильный экран `?view=pos` со своим `AppShell` и нижним таб-баром; сценарий — чистый редьюсер (`pos-logic.ts`) + хук с запросами и опросом (`use-pos-flow.ts`); правила долга выносятся в `lib/debt-orders.ts` и переиспользуются страницей долга.

**Tech Stack:** Django/DRF + pytest (ApiPay замокан через `urllib.request.urlopen`), Next 15.5 / React 19 / Tailwind v4, vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-09-12-cashier-pos-design.md`

## Global Constraints

- Коммитов в задачах 1–4 нет. Задача 5 — один коммит, пуш в текущую ветку и `main` (fast-forward), слежение за CI → деплоем: пользователь дал на это команду («как закончишь — пушни и следи CI/CD»).
- Kaspi QR через ApiPay: только `KZT`, только целые тенге (`qr_whole_tenge`, `apipay_kzt_only`).
- Портальный `create_client_payment` для кассы не использовать (переводит заказ в `settlement_intent = "instant"`).
- Без `channel` кассовый `kaspi` работает как раньше (отметка своего терминала, сразу `confirmed`, провайдер не вызывается).
- POS — только телефон (< 768px); десктоп не меняется (`?view=pos` → «Общее»).
- Токены темы, `shadow-card`; без декоративных анимаций. Русские тексты с «ё».
- Валюты не складываются.
- Prettier `printWidth: 120`; `eslint --max-warnings=0`; файлы с хуками начинаются с `"use client";`.
- В тестах суммы, отформатированные `formatMoney`/`formatCurrency`, содержат неразрывный пробел: `getByText("195 840 ₸")` работает (нормализатор схлопывает пробелы), а имена ролей сравниваются как есть — для них использовать регулярки (`{ name: /Показать QR/ }`).
- Бэкенд-тесты: `cd backend && .venv/bin/pytest <path> -q`; фронт: `cd frontend && npx vitest run <path>`.

---

### Task 1: Бэкенд — кассовый Kaspi QR и статус одной оплаты

**Files:**
- Modify: `backend/apps/orders/views.py` (импорты; helper рядом с `_issue_mixed_provider_payments`; ветка в действии `payments`; новое действие `payment_detail` перед `receive_payment`; карта `required_perms`)
- Create: `backend/apps/orders/tests/test_staff_pos_qr.py`

**Interfaces:**
- Produces: `POST /api/orders/{id}/payments/` с `{"method": "kaspi", "channel": "qr", "amount": "<целые тенге>"}` → 201 `PaymentSerializer` (`status: "requested"`, `provider.channel: "qr"`, `qr_image_url`, `qr_token_url`, `qr_expires_at`, `provider.status`); ошибки 400 `apipay_kzt_only`, `qr_whole_tenge`, провайдерские коды. `GET /api/orders/{id}/payments/{pid}/` → `PaymentSerializer` (права: `payments.create` или `payments.view`; чужой заказ → 404).

- [ ] **Step 1: Падающие тесты** — создать `backend/apps/orders/tests/test_staff_pos_qr.py`:

```python
"""POS кассы: Kaspi QR через ApiPay по заказу в долге и опрос статуса оплаты."""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import ApiPayAPIError
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


class ProviderResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


QR_RESPONSE = {
    "id": 77,
    "status": "pending",
    "qr_token_url": "https://qr.kaspi.kz/pos",
    "qr_image_url": "https://api.apipay.kz/qr/pos.png",
    "qr_expires_at": "2026-09-12T09:05:00+00:00",
}


def _debt_order(*, total="1000.00", currency="KZT"):
    client = Client.objects.create_with_user(
        first_name="Долговой",
        last_name="Клиент",
        phone="87762838451",
    )
    product = Product.objects.create(
        name="POS товар",
        color="Red",
        weight_kg="50",
        price=total,
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency=currency,
        settlement_intent="debt",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=Decimal(total),
    )
    return order


@pytest.fixture
def apipay(settings):
    settings.APIPAY_API_KEY = "server-only-key"
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    with patch("apps.orders.apipay.urllib.request.urlopen") as urlopen:
        urlopen.return_value = ProviderResponse(QR_RESPONSE)
        yield urlopen


def _pos_qr(auth_client, user, order, amount="1000"):
    return auth_client(user).post(
        f"/api/orders/{order.id}/payments/",
        {"method": "kaspi", "channel": "qr", "amount": amount},
        format="json",
    )


def test_pos_qr_issues_an_apipay_qr_and_keeps_the_debt(auth_client, accountant, apipay):
    order = _debt_order()

    response = _pos_qr(auth_client, accountant, order, amount="400")

    assert response.status_code == 201, response.data
    request = apipay.call_args.args[0]
    assert request.full_url == "https://api.apipay.kz/api/v1/invoices/qr"
    assert Decimal(str(json.loads(request.data)["amount"])) == Decimal("400")
    assert response.data["status"] == "requested"
    assert response.data["method"] == "kaspi"
    assert response.data["provider"]["channel"] == "qr"
    assert response.data["provider"]["qr_image_url"] == QR_RESPONSE["qr_image_url"]
    order.refresh_from_db()
    # Касса гасит долг: заказ не должен превратиться в «моментальную оплату».
    assert order.settlement_intent == "debt"
    assert order.is_debt


def test_pos_qr_is_tenge_only(auth_client, accountant, apipay):
    order = _debt_order(currency="USD")

    response = _pos_qr(auth_client, accountant, order)

    assert response.status_code == 400
    assert response.data["code"] == "apipay_kzt_only"
    assert not Payment.objects.filter(order=order).exists()
    apipay.assert_not_called()


def test_pos_qr_rejects_tiyn(auth_client, accountant, apipay):
    order = _debt_order()

    response = _pos_qr(auth_client, accountant, order, amount="10.50")

    assert response.status_code == 400
    assert response.data["code"] == "qr_whole_tenge"
    assert not Payment.objects.filter(order=order).exists()
    apipay.assert_not_called()


def test_pos_qr_provider_failure_rejects_the_payment(auth_client, accountant):
    order = _debt_order()

    with patch(
        "apps.orders.views.create_invoice",
        side_effect=ApiPayAPIError(503, "provider_unavailable", "Недоступно", {}),
    ):
        response = _pos_qr(auth_client, accountant, order)

    assert response.status_code == 400
    assert response.data["code"] == "provider_unavailable"
    assert set(order.payments.values_list("status", flat=True)) == {"rejected"}


def test_payment_detail_returns_the_provider_state(auth_client, accountant, apipay):
    order = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(accountant).get(
        f"/api/orders/{order.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 200
    assert response.data["id"] == created.data["id"]
    assert response.data["status"] == "requested"
    assert response.data["provider"]["qr_token_url"] == QR_RESPONSE["qr_token_url"]


def test_payment_detail_is_scoped_to_its_order(auth_client, accountant, apipay):
    order = _debt_order()
    other = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(accountant).get(
        f"/api/orders/{other.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 404


def test_payment_detail_requires_payment_permissions(
    auth_client, accountant, manager, apipay
):
    order = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(manager).get(
        f"/api/orders/{order.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 403
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd backend && .venv/bin/pytest apps/orders/tests/test_staff_pos_qr.py -q`
Expected: FAIL — QR-запрос проходит как кассовый `kaspi` (оплата сразу `confirmed`, провайдер не вызван), GET отдаёт 404/405.

- [ ] **Step 3: Импорты** — в `backend/apps/orders/views.py`:
  - `from decimal import Decimal` → `from decimal import Decimal, InvalidOperation`
  - в блок `from .services import (confirm_order, reject_order,` добавить `add_payment,` первым именем.

- [ ] **Step 4: Выдача QR кассой** — сразу после функции `_issue_mixed_provider_payments` добавить:

```python
def _issue_staff_qr_payment(order, amount_raw, user) -> Payment:
    """Касса выставляет Kaspi QR через ApiPay (POS на телефоне кассира).

    Заказ остаётся «в долг»: оплата создаётся кассовым add_payment, а не
    портальным create_client_payment — тот переводит заказ в моментальную
    оплату, и долг пропал бы из списка. Деньги подтверждает вебхук или сверка.
    """
    if order.currency != "KZT":
        raise ValidationError({
            "detail": "QR доступен только в тенге.",
            "code": "apipay_kzt_only",
        })
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, TypeError, ValueError):
        amount = None
    if (
        amount is None
        or not amount.is_finite()
        or amount <= 0
        or amount != amount.to_integral_value()
    ):
        raise ValidationError({
            "detail": "Kaspi QR принимает только целые тенге.",
            "code": "qr_whole_tenge",
        })
    payment = add_payment(order, amount, user, method="kaspi", stage="requested")
    try:
        create_invoice(payment, channel="qr", user=user)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
        _reject_created_payments([payment], user)
        raise _provider_error(exc) from exc
    payment.refresh_from_db()
    return payment
```

- [ ] **Step 5: Ветка в действии `payments`** — в одиночном пути сразу после строки `method = request.data.get("method") or "cash"` (перед комментарием «Канал счёта един…») вставить:

```python
        # POS кассы: Kaspi QR через ApiPay. Без channel кассовый kaspi — это
        # отметка о собственном терминале и подтверждается сразу, как раньше.
        if method == "kaspi" and request.data.get("channel") == "qr":
            payment = _issue_staff_qr_payment(
                order, request.data.get("amount"), request.user
            )
            return Response(PaymentSerializer(payment).data, status=201)
```

- [ ] **Step 6: Статус одной оплаты** — прямо перед `@action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/receive")` добавить:

```python
    @action(detail=True, methods=["get"], url_path=r"payments/(?P<pid>\d+)")
    def payment_detail(self, request, pk=None, pid=None):
        """Статус одной оплаты заказа: POS опрашивает его, пока клиент платит по QR."""
        order = self.get_object()
        payment = get_object_or_404(
            with_payment_api_relations(Payment.objects.filter(order=order)),
            pk=pid,
        )
        return Response(
            PaymentSerializer(payment, context={"request": request}).data
        )
```

и в `required_perms` после строки `"payments": "payments.create", "confirm": "orders.confirm",` добавить строку:

```python
        "payment_detail": ("payments.create", "payments.view"),
```

- [ ] **Step 7: Тесты зелёные, регрессий нет**

Run: `cd backend && .venv/bin/pytest apps/orders/tests/test_staff_pos_qr.py apps/orders/tests/test_payment_regressions.py apps/orders/tests/test_payment_autoconfirm.py apps/orders/tests/test_apipay.py -q`
Expected: всё PASS (в т.ч. `test_cashier_qr_never_calls_the_payment_provider`).

---

### Task 2: Общие модули — правила долга, клавиатура, QR-картинка

**Files:**
- Create: `frontend/src/lib/debt-orders.ts`, `frontend/src/lib/debt-orders.test.ts`, `frontend/src/components/ui/numpad.tsx`, `frontend/src/components/ui/numpad.test.tsx`, `frontend/src/components/transactions/qr-code-image.tsx`
- Modify: `frontend/src/app/accounting/debts/clients/[id]/page.tsx` (удалить локальные типы/функции, импортировать общие), `frontend/src/components/transactions/transaction-modals.tsx` (`PaymentQrPreview` на `QrCodeImage`)

**Interfaces:**
- Produces: `DebtStore`, `ClientDebtDetail`, `remainingOf(order)`, `pendingSum(order)`, `moneyCents(value)`, `availableCents(order)`, `blockingStore(order, stores)` из `@/lib/debt-orders`; `Numpad({ onDigit(digit: string), onBackspace(), disabled?, className? })` — кнопки «Цифра 0…9», «Стереть»; `QrCodeImage({ provider })` — `img` с alt «Kaspi QR для оплаты» или фолбэк.

- [ ] **Step 1: Падающие тесты** — `frontend/src/lib/debt-orders.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Order } from "@/lib/types";
import { availableCents, blockingStore, moneyCents, pendingSum, remainingOf, type DebtStore } from "./debt-orders";

function order(patch: Record<string, unknown> = {}): Order {
  return {
    id: 1,
    client: 1,
    currency: "KZT",
    status: "shipped",
    truck_number: "",
    items: [],
    total_amount: "1000",
    paid_total: "200",
    ...patch,
  } as unknown as Order;
}

const store: DebtStore = { id: 5, name: "Мерей", payment_schedule_type: "weekly", payment_days: [1], window_open: false };

describe("debt orders", () => {
  it("reads the remaining amount with a legacy fallback", () => {
    expect(remainingOf(order({ remaining_amount: "750.50" }))).toBe(750.5);
    expect(remainingOf(order())).toBe(800);
  });

  it("sums reservations and converts money to cents", () => {
    const withPending = order({ pending_payments: [{ amount: "100.25" }, { amount: "50" }] });
    expect(pendingSum(withPending)).toBe(150.25);
    expect(moneyCents("12.34")).toBe(1234);
    expect(moneyCents("abc")).toBe(0);
  });

  it("subtracts reservations from what can still be taken", () => {
    expect(availableCents(order({ pending_payments: [{ amount: "300" }] }))).toBe(50000);
    expect(availableCents(order({ pending_payments: [{ amount: "900" }] }))).toBe(0);
  });

  it("blocks only orders of stores whose payment window is closed", () => {
    expect(blockingStore(order(), [store])).toBeNull();
    expect(blockingStore(order({ store: 5 }), [store])).toBe(store);
    expect(blockingStore(order({ store: 5 }), [{ ...store, window_open: true }])).toBeNull();
    expect(blockingStore(order({ store: 5 }), [{ ...store, payment_schedule_type: "none" }])).toBeNull();
    expect(blockingStore(order({ store: 9 }), [store])).toBeNull();
  });
});
```

`frontend/src/components/ui/numpad.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Numpad } from "./numpad";

it("reports digits and backspace", async () => {
  const user = userEvent.setup();
  const onDigit = vi.fn();
  const onBackspace = vi.fn();
  render(<Numpad onDigit={onDigit} onBackspace={onBackspace} />);

  await user.click(screen.getByRole("button", { name: "Цифра 7" }));
  await user.click(screen.getByRole("button", { name: "Цифра 0" }));
  await user.click(screen.getByRole("button", { name: "Стереть" }));

  expect(onDigit.mock.calls).toEqual([["7"], ["0"]]);
  expect(onBackspace).toHaveBeenCalledTimes(1);
});

it("disables every key while busy", () => {
  render(<Numpad onDigit={vi.fn()} onBackspace={vi.fn()} disabled />);
  const keys = screen.getAllByRole("button");
  expect(keys).toHaveLength(11);
  keys.forEach((key) => expect(key).toBeDisabled());
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/lib/debt-orders.test.ts src/components/ui/numpad.test.tsx`
Expected: FAIL — модулей нет.

- [ ] **Step 3: `lib/debt-orders.ts`** — перенести со страницы долга (тела функций те же) и добавить два правила:

```ts
import type { Client, Order } from "@/lib/types";

/** Магазин клиента с расписанием оплат: вне окна оплата по его заказам закрыта. */
export interface DebtStore {
  id: number;
  name: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
  window_open: boolean;
}

/** Ответ `/clients/{id}/debt-detail/`. */
export interface ClientDebtDetail {
  client: Client;
  /** Суммы в основной валюте клиента — её же показывают плитки сверху. */
  debt_total: string;
  debt_currency?: "KZT" | "USD";
  debt_by_currency?: Record<string, string>;
  lifetime_total?: string;
  lifetime_paid?: string;
  lifetime_by_currency?: Record<string, { total: string; paid: string }>;
  overdue_total?: string;
  overdue_by_currency?: Record<string, string>;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores: DebtStore[];
  orders: Order[];
}

export function remainingOf(order: Order): number {
  return Number(order.remaining_amount ?? Number(order.total_amount) - Number(order.paid_total));
}

/** Уже зарезервировано незавершёнными оплатами (ждут клиента или кассу). */
export function pendingSum(order: Order): number {
  return (order.pending_payments ?? []).reduce((s, p) => s + Number(p.amount), 0);
}

export function moneyCents(value: number | string): number {
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.round(amount * 100) : 0;
}

/** Сколько ещё можно принять по заказу, в тиынах: остаток минус резервы. */
export function availableCents(order: Order): number {
  return Math.max(0, moneyCents(remainingOf(order)) - moneyCents(pendingSum(order)));
}

/** Магазин, чьё закрытое окно оплаты блокирует заказ; null — оплата открыта. */
export function blockingStore(order: Order, stores: readonly DebtStore[]): DebtStore | null {
  if (order.store == null) return null;
  const store = stores.find((row) => row.id === order.store);
  if (!store || store.payment_schedule_type === "none" || store.window_open) return null;
  return store;
}
```

- [ ] **Step 4: Страница долга на общих правилах** — в `frontend/src/app/accounting/debts/clients/[id]/page.tsx`:
  - удалить локальные `interface DebtStore {…}`, `interface ClientDebtDetail {…}` и функции `remainingOf`, `pendingSum`, `moneyCents`;
  - добавить импорт `import { blockingStore, moneyCents, pendingSum, remainingOf, type ClientDebtDetail, type DebtStore } from "@/lib/debt-orders";` (оставить только реально используемые имена — eslint подскажет);
  - в `ClientDebtPageInner` заменить `const storeById = new Map(…)` и функцию `blockedFor` целиком на
    ```ts
    // Магазин с расписанием блокирует оплату вне окна.
    const blockedFor = (order: Order): DebtStore | null => blockingStore(order, data.stores);
    ```
  - если импорт `Client` стал неиспользуемым — убрать.
  Поведение страницы не меняется; её тест `page.test.tsx` должен пройти без правок.

- [ ] **Step 5: Клавиатура** — `frontend/src/components/ui/numpad.tsx`:

```tsx
"use client";
import { Delete } from "lucide-react";
import { cn } from "@/lib/utils";

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const KEY =
  "flex h-14 items-center justify-center rounded-xl text-[26px] font-medium tabular-nums transition-colors " +
  "focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50 disabled:opacity-40";
const DIGIT_KEY = cn(KEY, "bg-[var(--muted)] text-[var(--foreground)] hover:bg-[var(--border)] active:bg-[var(--border)]");

/** Цифровая клавиатура суммы, как в Kaspi POS: 1–9, 0 и «стереть». */
export function Numpad({
  onDigit,
  onBackspace,
  disabled = false,
  className,
}: {
  onDigit: (digit: string) => void;
  onBackspace: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-3 gap-2", className)}>
      {DIGITS.map((digit) => (
        <button
          key={digit}
          type="button"
          aria-label={`Цифра ${digit}`}
          disabled={disabled}
          onClick={() => onDigit(digit)}
          className={DIGIT_KEY}
        >
          {digit}
        </button>
      ))}
      <span aria-hidden />
      <button type="button" aria-label="Цифра 0" disabled={disabled} onClick={() => onDigit("0")} className={DIGIT_KEY}>
        0
      </button>
      <button
        type="button"
        aria-label="Стереть"
        disabled={disabled}
        onClick={onBackspace}
        className={cn(KEY, "bg-[var(--secondary)] text-[var(--foreground)] hover:bg-[var(--border)]")}
      >
        <Delete className="size-6" aria-hidden />
      </button>
    </div>
  );
}
```

- [ ] **Step 6: QR-картинка общая** — `frontend/src/components/transactions/qr-code-image.tsx`:

```tsx
"use client";
import Image from "next/image";
import { useState } from "react";
import { QrCode } from "lucide-react";
import type { Payment } from "@/lib/types";

/** Kaspi QR платёжного сервиса; без картинки — подсказка открыть оплату кнопкой. */
export function QrCodeImage({ provider }: { provider: NonNullable<Payment["provider"]> }) {
  const [failed, setFailed] = useState(false);
  if (provider.qr_image_url && !failed) {
    return (
      <Image
        src={provider.qr_image_url}
        alt="Kaspi QR для оплаты"
        width={288}
        height={288}
        unoptimized
        onError={() => setFailed(true)}
        className="mx-auto size-72 max-w-full rounded-2xl bg-white p-3 shadow-sm"
      />
    );
  }
  return (
    <div className="mx-auto flex aspect-square w-72 max-w-full flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--muted)]/35 p-6">
      <QrCode className="size-12 text-[var(--muted-foreground)]" />
      <p className="mt-3 text-sm text-[var(--muted-foreground)]">
        Изображение QR недоступно. Откройте оплату кнопкой ниже.
      </p>
    </div>
  );
}
```

В `frontend/src/components/transactions/transaction-modals.tsx` внутри `PaymentQrPreview` заменить весь условный блок `{provider.qr_image_url && !imageFailed ? (<Image …/>) : (<div …>…</div>)}` на `<QrCodeImage provider={provider} />`, удалить `const [imageFailed, setImageFailed] = useState(false);` и ставшие неиспользуемыми импорты (`Image`, `QrCode`, `useState` — если больше не нужны), добавить `import { QrCodeImage } from "./qr-code-image";`. Разметка модалки (заголовок, кнопка «Открыть Kaspi», футер) не меняется.

- [ ] **Step 7: Тесты зелёные**

Run: `cd frontend && npx vitest run src/lib/debt-orders.test.ts src/components/ui/numpad.test.tsx "src/app/accounting/debts" src/components/transactions src/components/transactions-section.test.tsx && npx tsc --noEmit && npx eslint src/lib src/components/ui src/components/transactions "src/app/accounting/debts" --max-warnings=0`
Expected: PASS, чисто.

---

### Task 3: Логика POS — редьюсер и хук

**Files:**
- Create: `frontend/src/components/cashier/mobile/pos/pos-logic.ts`, `pos-logic.test.ts`, `use-pos-flow.ts`

**Interfaces:**
- Consumes: `availableCents`, `blockingStore`, `DebtStore`, `ClientDebtDetail` (Task 2); `useApi`, `api`, `apiError`, `useVisiblePolling`.
- Produces: `PosTab`, `PosFlow`, `PosStep`, `PosState`, `PosAction`, `INITIAL_POS_STATE`, `posReducer`, `appendDigit`, `eraseDigit`, `wholeTengeLimit`, `posOrderBlock`, `paymentOutcome`, `PaymentOutcome`; `usePosFlow({ onPaid? })` → `PosFlowApi` (`state, busy, detail, order, outcome, canGoBack, setTab, pickClient, pickOrder, digit, erase, fillAll, toPhone, setPhone, issueQr, sendInvoice, back, retry, reset`).

- [ ] **Step 1: Падающие тесты** — `frontend/src/components/cashier/mobile/pos/pos-logic.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Order, Payment } from "@/lib/types";
import {
  INITIAL_POS_STATE,
  appendDigit,
  eraseDigit,
  paymentOutcome,
  posOrderBlock,
  posReducer,
  wholeTengeLimit,
  type PosState,
} from "./pos-logic";

function order(patch: Record<string, unknown> = {}): Order {
  return {
    id: 130,
    client: 1,
    currency: "KZT",
    status: "shipped",
    truck_number: "",
    items: [],
    total_amount: "195840.50",
    paid_total: "0",
    remaining_amount: "195840.50",
    pending_payments: [],
    ...patch,
  } as unknown as Order;
}

function payment(patch: Record<string, unknown> = {}): Payment {
  return {
    id: 501,
    order: 130,
    amount: "100.00",
    currency: "KZT",
    method: "kaspi",
    status: "requested",
    paid_at: "2026-09-12T10:00:00",
    recorded_by: 1,
    provider: null,
    ...patch,
  } as unknown as Payment;
}

describe("amount keypad", () => {
  it("never starts with zero and never exceeds the limit", () => {
    expect(appendDigit("", "0", 1000)).toBe("");
    expect(appendDigit("", "5", 1000)).toBe("5");
    expect(appendDigit("12", "3", 1000)).toBe("123");
    expect(appendDigit("999", "9", 1000)).toBe("999");
    expect(appendDigit("12", "x", 1000)).toBe("12");
    expect(eraseDigit("123")).toBe("12");
    expect(eraseDigit("")).toBe("");
  });

  it("splits what QR can take into whole tenge and leftover tiyn", () => {
    expect(wholeTengeLimit(order())).toEqual({ max: 195840, tiyn: 50 });
    expect(wholeTengeLimit(order({ pending_payments: [{ amount: "195840.50" }] }))).toEqual({ max: 0, tiyn: 0 });
  });
});

describe("posOrderBlock", () => {
  it("explains why an order cannot be paid by QR", () => {
    expect(posOrderBlock(order({ currency: "USD" }), [])).toBe("QR только в тенге");
    expect(
      posOrderBlock(order({ store: 5 }), [
        { id: 5, name: "Мерей", payment_schedule_type: "weekly", payment_days: [1], window_open: false },
      ]),
    ).toBe("Оплата для магазина «Мерей» сегодня недоступна");
    expect(posOrderBlock(order({ pending_payments: [{ amount: "195840.50" }] }), [])).toBe(
      "Всё уже ожидает подтверждения",
    );
    expect(posOrderBlock(order({ remaining_amount: "0.40" }), [])).toBe("Нечего оплачивать");
    expect(posOrderBlock(order(), [])).toBeNull();
  });
});

describe("paymentOutcome", () => {
  it("maps payment and provider states", () => {
    expect(paymentOutcome(payment({ status: "confirmed" }))).toBe("paid");
    expect(paymentOutcome(payment({ status: "rejected" }))).toBe("failed");
    expect(paymentOutcome(payment({ provider: { status: "expired" } }))).toBe("failed");
    expect(paymentOutcome(payment({ provider: { status: "pending" } }))).toBe("waiting");
  });
});

describe("posReducer", () => {
  const picked: PosState = posReducer(posReducer(INITIAL_POS_STATE, { type: "client", id: 1, name: "Асан" }), {
    type: "order",
    id: 130,
    amount: "195840",
  });

  it("walks client → order → amount and back", () => {
    expect(picked).toMatchObject({ step: "amount", clientId: 1, clientName: "Асан", orderId: 130, amount: "195840" });
    const toOrder = posReducer(picked, { type: "back" });
    expect(toOrder).toMatchObject({ step: "order", orderId: null, amount: "" });
    expect(posReducer(toOrder, { type: "back" })).toMatchObject({ step: "client", clientId: null });
    expect(posReducer(INITIAL_POS_STATE, { type: "back" })).toBe(INITIAL_POS_STATE);
  });

  it("keeps the selection when switching payment modes before issuing", () => {
    const remote = posReducer(picked, { type: "tab", tab: "remote" });
    expect(remote).toMatchObject({ tab: "remote", flow: "remote", step: "amount", orderId: 130 });
    const phone = posReducer(remote, { type: "phone-step", phone: "87011234567" });
    expect(phone).toMatchObject({ step: "phone", phone: "87011234567" });
    expect(posReducer(phone, { type: "tab", tab: "qr" })).toMatchObject({ flow: "qr", step: "amount" });
  });

  it("starts over when the mode changes after a QR was issued, but survives a look at history", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    expect(issued.step).toBe("result");
    const history = posReducer(issued, { type: "tab", tab: "history" });
    expect(history).toMatchObject({ tab: "history", flow: "qr", step: "result" });
    expect(posReducer(history, { type: "tab", tab: "qr" })).toMatchObject({ tab: "qr", step: "result" });
    expect(posReducer(issued, { type: "tab", tab: "remote" })).toMatchObject({
      tab: "remote",
      flow: "remote",
      step: "client",
      payment: null,
    });
  });

  it("updates only the issued payment, retries from the amount and resets to a new client", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    expect(posReducer(issued, { type: "payment", payment: payment({ id: 999, status: "confirmed" }) })).toBe(issued);
    expect(
      posReducer(issued, { type: "payment", payment: payment({ status: "confirmed" }) }).payment?.status,
    ).toBe("confirmed");
    expect(posReducer(issued, { type: "retry" })).toMatchObject({ step: "amount", payment: null, orderId: 130 });
    expect(posReducer(issued, { type: "reset" })).toMatchObject({ step: "client", clientId: null, flow: "qr" });
  });

  it("clears the error on the next edit", () => {
    const failed = posReducer(picked, { type: "error", error: "Сервис недоступен" });
    expect(failed.error).toBe("Сервис недоступен");
    expect(posReducer(failed, { type: "amount", amount: "10" }).error).toBe("");
  });
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/cashier/mobile/pos`
Expected: FAIL — модуля нет.

- [ ] **Step 3: `pos-logic.ts`**:

```ts
import { availableCents, blockingStore, type DebtStore } from "@/lib/debt-orders";
import type { Order, Payment } from "@/lib/types";

export type PosTab = "qr" | "remote" | "history";
export type PosFlow = Exclude<PosTab, "history">;
export type PosStep = "client" | "order" | "amount" | "phone" | "result";

export interface PosState {
  /** Видимый таб нижней панели. */
  tab: PosTab;
  /** Режим текущей оплаты: QR на экране кассира или счёт на телефон клиента. */
  flow: PosFlow;
  step: PosStep;
  clientId: number | null;
  clientName: string;
  orderId: number | null;
  /** Сумма целыми тенге, цифрами; "" — ноль. */
  amount: string;
  phone: string;
  /** Выданная оплата (QR или счёт) — её статус опрашивается. */
  payment: Payment | null;
  error: string;
}

export const INITIAL_POS_STATE: PosState = {
  tab: "qr",
  flow: "qr",
  step: "client",
  clientId: null,
  clientName: "",
  orderId: null,
  amount: "",
  phone: "",
  payment: null,
  error: "",
};

export type PosAction =
  | { type: "tab"; tab: PosTab }
  | { type: "client"; id: number; name: string }
  | { type: "order"; id: number; amount: string }
  | { type: "amount"; amount: string }
  | { type: "phone-step"; phone: string }
  | { type: "phone"; phone: string }
  | { type: "issued"; payment: Payment }
  | { type: "payment"; payment: Payment }
  | { type: "error"; error: string }
  | { type: "back" }
  | { type: "retry" }
  | { type: "reset" };

const MAX_DIGITS = 12;
const CLOSED_PROVIDER_STATUSES = new Set(["expired", "cancelled", "error", "superseded"]);

/** Цифра с клавиатуры: без ведущего нуля и не больше доступного. */
export function appendDigit(amount: string, digit: string, max: number): string {
  if (!/^\d$/.test(digit)) return amount;
  const next = amount === "" ? (digit === "0" ? "" : digit) : amount + digit;
  if (next.length > MAX_DIGITS || Number(next || "0") > max) return amount;
  return next;
}

export function eraseDigit(amount: string): string {
  return amount.slice(0, -1);
}

/** Сколько целых тенге можно взять по QR и сколько тиынов останется на другой способ. */
export function wholeTengeLimit(order: Order): { max: number; tiyn: number } {
  const cents = availableCents(order);
  return { max: Math.floor(cents / 100), tiyn: cents % 100 };
}

/** Почему заказ нельзя оплатить в POS; null — можно. */
export function posOrderBlock(order: Order, stores: readonly DebtStore[]): string | null {
  if (order.currency !== "KZT") return "QR только в тенге";
  const store = blockingStore(order, stores);
  if (store) return `Оплата для магазина «${store.name}» сегодня недоступна`;
  if (wholeTengeLimit(order).max < 1) {
    return (order.pending_payments ?? []).length > 0 ? "Всё уже ожидает подтверждения" : "Нечего оплачивать";
  }
  return null;
}

export type PaymentOutcome = "waiting" | "paid" | "failed";

/** Итог выданной оплаты: деньги пришли, QR/счёт закрыт без денег или ждём клиента. */
export function paymentOutcome(payment: Payment): PaymentOutcome {
  if (payment.status === "confirmed") return "paid";
  if (payment.status === "rejected" || (payment.provider && CLOSED_PROVIDER_STATUSES.has(payment.provider.status))) {
    return "failed";
  }
  return "waiting";
}

function freshState(flow: PosFlow): PosState {
  return { ...INITIAL_POS_STATE, tab: flow, flow };
}

export function posReducer(state: PosState, action: PosAction): PosState {
  switch (action.type) {
    case "tab": {
      if (action.tab === "history") return { ...state, tab: "history" };
      if (action.tab === state.flow) return { ...state, tab: action.tab };
      // Выданный QR или счёт не переносится в другой режим — начинаем новую оплату.
      if (state.step === "result") return freshState(action.tab);
      return { ...state, tab: action.tab, flow: action.tab, step: state.step === "phone" ? "amount" : state.step, error: "" };
    }
    case "client":
      return {
        ...state,
        step: "order",
        clientId: action.id,
        clientName: action.name,
        orderId: null,
        amount: "",
        payment: null,
        error: "",
      };
    case "order":
      return { ...state, step: "amount", orderId: action.id, amount: action.amount, error: "" };
    case "amount":
      return { ...state, amount: action.amount, error: "" };
    case "phone-step":
      return { ...state, step: "phone", phone: state.phone || action.phone, error: "" };
    case "phone":
      return { ...state, phone: action.phone, error: "" };
    case "issued":
      return { ...state, step: "result", payment: action.payment, error: "" };
    case "payment":
      return state.payment && state.payment.id === action.payment.id ? { ...state, payment: action.payment } : state;
    case "error":
      return { ...state, error: action.error };
    case "back":
      switch (state.step) {
        case "order":
          return { ...state, step: "client", clientId: null, clientName: "", orderId: null, amount: "", error: "" };
        case "amount":
          return { ...state, step: "order", orderId: null, amount: "", error: "" };
        case "phone":
          return { ...state, step: "amount", error: "" };
        case "result":
          return freshState(state.flow);
        default:
          return state;
      }
    case "retry":
      return { ...state, step: "amount", payment: null, error: "" };
    case "reset":
      return freshState(state.flow);
  }
}
```

- [ ] **Step 4: `use-pos-flow.ts`**:

```ts
"use client";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import type { ClientDebtDetail } from "@/lib/debt-orders";
import type { Payment } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import {
  INITIAL_POS_STATE,
  appendDigit,
  eraseDigit,
  paymentOutcome,
  posReducer,
  wholeTengeLimit,
  type PosTab,
} from "./pos-logic";

/** Как часто спрашиваем статус выданного QR или счёта. */
export const POS_POLL_MS = 3_000;

/** Сценарий POS: клиент → заказ → сумма → QR или счёт, опрос статуса до «Оплачено». */
export function usePosFlow({ onPaid }: { onPaid?: () => void } = {}) {
  const [state, dispatch] = useReducer(posReducer, INITIAL_POS_STATE);
  const [busy, setBusy] = useState(false);
  const detail = useApi<ClientDebtDetail>(state.clientId ? `/clients/${state.clientId}/debt-detail/` : null);
  const order = detail.data?.orders.find((row) => row.id === state.orderId) ?? null;
  const limit = order ? wholeTengeLimit(order).max : 0;
  const payment = state.payment;
  const outcome = payment ? paymentOutcome(payment) : null;

  const issue = useCallback(
    async (body: Record<string, string>) => {
      if (!state.orderId || busy) return;
      setBusy(true);
      try {
        const response = await api.post<Payment>(`/orders/${state.orderId}/payments/`, body);
        dispatch({ type: "issued", payment: response.data });
      } catch (e) {
        dispatch({ type: "error", error: apiError(e) });
      } finally {
        setBusy(false);
      }
    },
    [busy, state.orderId],
  );

  const poll = useCallback(async () => {
    if (!payment) return;
    const response = await api.get<Payment>(`/orders/${payment.order}/payments/${payment.id}/`);
    dispatch({ type: "payment", payment: response.data });
  }, [payment]);
  useVisiblePolling(poll, POS_POLL_MS, state.step === "result" && outcome === "waiting");

  // Деньги пришли — один раз на оплату обновляем долги клиента и общий список.
  const paidFor = useRef<number | null>(null);
  const { reload: reloadDetail } = detail;
  useEffect(() => {
    if (!payment || outcome !== "paid" || paidFor.current === payment.id) return;
    paidFor.current = payment.id;
    void reloadDetail();
    onPaid?.();
  }, [onPaid, outcome, payment, reloadDetail]);

  return {
    state,
    busy,
    detail,
    order,
    outcome,
    canGoBack: state.tab !== "history" && state.step !== "client",
    setTab: (tab: PosTab) => dispatch({ type: "tab", tab }),
    pickClient: (id: number, name: string) => dispatch({ type: "client", id, name }),
    pickOrder: (id: number) => {
      const target = detail.data?.orders.find((row) => row.id === id);
      const max = target ? wholeTengeLimit(target).max : 0;
      dispatch({ type: "order", id, amount: max > 0 ? String(max) : "" });
    },
    digit: (digit: string) => dispatch({ type: "amount", amount: appendDigit(state.amount, digit, limit) }),
    erase: () => dispatch({ type: "amount", amount: eraseDigit(state.amount) }),
    fillAll: () => dispatch({ type: "amount", amount: limit > 0 ? String(limit) : "" }),
    toPhone: () => dispatch({ type: "phone-step", phone: detail.data?.client.phone ?? "" }),
    setPhone: (phone: string) => dispatch({ type: "phone", phone }),
    issueQr: () => void issue({ method: "kaspi", channel: "qr", amount: state.amount }),
    sendInvoice: () => void issue({ method: "invoice", amount: state.amount, phone_number: state.phone }),
    back: () => dispatch({ type: "back" }),
    retry: () => dispatch({ type: "retry" }),
    reset: () => dispatch({ type: "reset" }),
  };
}

export type PosFlowApi = ReturnType<typeof usePosFlow>;
```

- [ ] **Step 5: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier/mobile/pos && npx tsc --noEmit && npx eslint src/components/cashier/mobile/pos --max-warnings=0`
Expected: PASS, чисто.

---

### Task 4: Экран POS, кнопка POS и подключение к кассе

**Files:**
- Create: `frontend/src/components/cashier/mobile/pos/pos-steps.tsx`, `pos-result.tsx`, `pos-fab.tsx`, `pos-screen.tsx`, `pos-screen.test.tsx`
- Modify: `frontend/src/components/cashier/view.ts`, `view.test.ts`, `frontend/src/components/cashier/use-cashier.ts`, `frontend/src/components/cashier/mobile/mobile-cashier.tsx`

**Interfaces:**
- Consumes: `usePosFlow`, `PosFlowApi`, `posOrderBlock`, `wholeTengeLimit`, `PaymentOutcome`, `PosTab` (Task 3); `Numpad`, `QrCodeImage`, `remainingOf`, `ClientDebtDetail` (Task 2); `CashierModel` (`perms`, `debts`, `debtRows`), `TransactionsScreen`, `matchesDebtQuery`, `AppShell` с `back`.
- Produces: `PosScreen({ model, onClose })`, `PosFab({ onClick })`; `CashView` включает `"pos"`.

- [ ] **Step 1: Падающие тесты** — в `frontend/src/components/cashier/view.test.ts` в `describe("resolveView")` добавить:

```ts
  it("opens POS only on phones and only with payments.create", () => {
    expect(resolveView("pos", all, true)).toBe("pos");
    expect(resolveView("pos", all, false)).toBe("overview");
    expect(resolveView("pos", viewer, true)).toBe("transactions");
  });
```

и создать `frontend/src/components/cashier/mobile/pos/pos-screen.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, expect, it, vi } from "vitest";
import CashierPage from "@/app/accounting/page";
import type { TopbarBack } from "@/components/layout/topbar";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: { is_superuser: true, permissions: [] as string[] },
  poll: null as null | (() => Promise<unknown>),
  paymentStatus: "requested",
}));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number, active: boolean) => {
    if (active && intervalMs === 3_000) mocks.poll = poll;
  },
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ title, back, children }: { title: string; back?: TopbarBack; children: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {back && (
        <button type="button" onClick={back.onClick}>
          {back.label}
        </button>
      )}
      {children}
    </div>
  ),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));
vi.mock("next/image", () => ({
  // eslint-disable-next-line @next/next/no-img-element
  default: ({ src, alt }: { src: string; alt: string }) => <img src={src} alt={alt} />,
}));

const debtors = [
  {
    client_id: 1,
    client_name: "Асан Бекмуратов",
    client_phone: "87011234567",
    debt_total: "195840",
    debt_currency: "KZT",
    debt_by_currency: { KZT: "195840" },
    orders_count: 2,
    unpaid_count: 2,
    partial_count: 0,
    stores_count: 0,
    overdue_count: 0,
  },
  {
    client_id: 2,
    client_name: "Мерей Ахметова",
    client_phone: "87779998877",
    debt_total: "5000",
    debt_currency: "KZT",
    debt_by_currency: { KZT: "5000" },
    orders_count: 1,
    unpaid_count: 1,
    partial_count: 0,
    stores_count: 0,
    overdue_count: 0,
  },
];

const debtOrder = (id: number, currency: string, total: string) => ({
  id,
  client: 1,
  currency,
  status: "shipped",
  department_name: "Мельница",
  total_amount: total,
  paid_total: "0",
  remaining_amount: total,
  pending_payments: [],
  items: [],
  truck_number: "",
});

const detail = {
  client: { id: 1, name: "Асан Бекмуратов", phone: "87011234567", currency: "KZT" },
  debt_total: "195840",
  orders_count: 2,
  unpaid_count: 2,
  partial_count: 0,
  stores: [],
  orders: [debtOrder(130, "KZT", "195840"), debtOrder(131, "USD", "500")],
};

function qrPayment(status: string) {
  return {
    id: 501,
    order: 130,
    amount: "195840.00",
    currency: "KZT",
    method: "kaspi",
    status,
    paid_at: "2026-09-12T10:00:00",
    recorded_by: 1,
    provider: {
      invoice_id: 77,
      channel: "qr",
      status: status === "confirmed" ? "paid" : "pending",
      phone_number: null,
      qr_token_url: "https://qr.kaspi.kz/pos",
      qr_image_url: "https://api.apipay.kz/qr/pos.png",
      qr_expires_at: "2026-09-12T10:05:00",
      total_refunded: "0.00",
      available_for_refund: "0.00",
      refunds: [],
    },
  };
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
});

beforeEach(() => {
  resetNavigation("/accounting?view=pos");
  mocks.me = { is_superuser: true, permissions: [] };
  mocks.poll = null;
  mocks.paymentStatus = "requested";
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: qrPayment("requested") });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/debts/") return { data: debtors };
    if (url.pathname === "/clients/1/debt-detail/") return { data: detail };
    if (url.pathname === "/orders/130/payments/501/") return { data: qrPayment(mocks.paymentStatus) };
    if (url.pathname === "/payment-transactions/")
      return {
        data: {
          results: [],
          page: 1,
          pages: 1,
          count: 0,
          status_counts: {},
          summary: {
            paid_by_currency: { KZT: "0", USD: "0" },
            refunded_by_currency: { KZT: "0", USD: "0" },
            paid_by_method: {},
          },
        },
      };
    return { data: [] };
  });
});

async function pickFirstOrder(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));
  await user.click(await screen.findByRole("button", { name: /Заказ #130/ }));
}

it("takes a debt payment by Kaspi QR", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);

  await user.type(await screen.findByPlaceholderText("Поиск клиента: имя или телефон"), "Асан");
  expect(screen.queryByText("Мерей Ахметова")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Асан Бекмуратов/ }));
  expect(await screen.findByRole("button", { name: /Заказ #131/ })).toBeDisabled();
  expect(screen.getByText("QR только в тенге")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Заказ #130/ }));

  expect(screen.getByText("195 840 ₸")).toBeInTheDocument();
  for (let i = 0; i < 3; i += 1) await user.click(screen.getByRole("button", { name: "Стереть" }));
  expect(screen.getByText("195 ₸")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Цифра 0" }));
  expect(screen.getByText("1 950 ₸")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Весь остаток" }));
  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(mocks.post).toHaveBeenCalledWith("/orders/130/payments/", {
    method: "kaspi",
    channel: "qr",
    amount: "195840",
  });
  expect(await screen.findByRole("img", { name: "Kaspi QR для оплаты" })).toHaveAttribute(
    "src",
    "https://api.apipay.kz/qr/pos.png",
  );
  expect(screen.getByText("Ожидаем оплату…")).toBeInTheDocument();

  mocks.paymentStatus = "confirmed";
  await act(async () => {
    await mocks.poll?.();
  });
  expect(await screen.findByRole("heading", { name: "Оплачено" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Новая оплата" }));
  expect(await screen.findByPlaceholderText("Поиск клиента: имя или телефон")).toBeInTheDocument();
});

it("keeps the amount when the QR cannot be created", async () => {
  const user = userEvent.setup();
  mocks.post.mockRejectedValue(new Error("Платёжный сервис недоступен"));
  render(<CashierPage />);
  await pickFirstOrder(user);

  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Платёжный сервис недоступен");
  expect(screen.getByText("195 840 ₸")).toBeInTheDocument();
});

it("sends a Kaspi invoice to the client's phone", async () => {
  const user = userEvent.setup();
  const invoice = qrPayment("requested");
  mocks.post.mockResolvedValue({
    data: {
      ...invoice,
      method: "invoice",
      provider: {
        ...invoice.provider,
        channel: "phone",
        phone_number: "87011234567",
        qr_image_url: null,
        qr_token_url: null,
      },
    },
  });
  render(<CashierPage />);

  await user.click(await screen.findByRole("tab", { name: /Удаленно/ }));
  expect(screen.getByRole("heading", { name: "Удаленная оплата" })).toBeInTheDocument();
  await pickFirstOrder(user);
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByLabelText("Телефон покупателя")).toHaveValue("87011234567");
  await user.click(screen.getByRole("button", { name: /Отправить счёт/ }));

  expect(mocks.post).toHaveBeenCalledWith("/orders/130/payments/", {
    method: "invoice",
    amount: "195840",
    phone_number: "87011234567",
  });
  expect(await screen.findByText(/Счёт отправлен на 87011234567/)).toBeInTheDocument();
});

it("steps back inside POS and closes it from the first step", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));
  await screen.findByRole("button", { name: /Заказ #130/ });

  await user.click(screen.getByRole("button", { name: "Назад" }));
  expect(await screen.findByPlaceholderText("Поиск клиента: имя или телефон")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Закрыть POS" }));
  expect(routerCalls.replace).toContain("/accounting");
});

it("shows the transactions history inside POS", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("tab", { name: /История/ }));
  expect(screen.getByRole("heading", { name: "История" })).toBeInTheDocument();
  expect(await screen.findByText("Транзакций пока нет.")).toBeInTheDocument();
});

it("offers the POS button only to staff who can take payments", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting");
  const { unmount } = render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть POS" }));
  expect(routerCalls.push).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  unmount();

  resetNavigation("/accounting");
  mocks.me = { is_superuser: false, permissions: ["payments.confirm", "payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Касса" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Открыть POS" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/cashier/view.test.ts src/components/cashier/mobile/pos/pos-screen.test.tsx`
Expected: FAIL — `pos` неизвестен, экрана нет.

- [ ] **Step 3: Экран в URL** — `frontend/src/components/cashier/view.ts`:
  - комментарий и тип: `/** Экран кассы: десктоп знает overview/confirm/journal/transactions, телефон — home/report/debts/confirm/journal/transactions/pos. */` и `export type CashView = "home" | "overview" | "report" | "debts" | "confirm" | "journal" | "transactions" | "pos";`
  - `export type MobileMenuKey = Exclude<CashView, "home" | "overview" | "pos">;`
  - в `viewAllowed` перед `case "transactions":` добавить `case "pos": return perms.canCreatePayments;` (отдельной веткой);
  - `ALL_VIEWS` дополнить `"pos"`;
  - в `resolveView` условие десктопа: `if (!mobile && (view === "home" || view === "report" || view === "debts" || view === "pos")) view = "overview";`

- [ ] **Step 4: Данные для поиска клиента** — `frontend/src/components/cashier/use-cashier.ts`: после `const debtsActive = mobile && view === "debts";` добавить `const posActive = mobile && view === "pos";`, а последнюю ветку `debtsFilters` заменить на `: homeActive || posActive ? EMPTY_CASH_FILTERS : null;` и дополнить комментарий хука: «POS — список должников без фильтров для поиска клиента.»

- [ ] **Step 5: Шаги POS** — `frontend/src/components/cashier/mobile/pos/pos-steps.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Numpad } from "@/components/ui/numpad";
import { remainingOf, type ClientDebtDetail } from "@/lib/debt-orders";
import type { ClientDebt, Order } from "@/lib/types";
import { formatCurrency, formatMoney } from "@/lib/utils";
import { matchesDebtQuery } from "../../debt-state";
import { posOrderBlock, wholeTengeLimit } from "./pos-logic";

const LIST =
  "divide-y divide-[var(--border)] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";
const ROW =
  "flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60 disabled:cursor-not-allowed disabled:opacity-60";
const NOTE = "py-8 text-center text-sm text-[var(--muted-foreground)]";

/** Шаг 1: кого обслуживаем — поиск по должникам. */
export function PosClientStep({
  rows,
  loading,
  error,
  onRetry,
  onPick,
}: {
  rows: ClientDebt[];
  loading: boolean;
  error: string;
  onRetry: () => void;
  onPick: (id: number, name: string) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = rows.filter((row) => matchesDebtQuery(row, query)).slice(0, 50);
  return (
    <section className="flex flex-col gap-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input
          autoFocus
          className="h-11 pl-9 text-base"
          placeholder="Поиск клиента: имя или телефон"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {loading && rows.length === 0 ? (
        <p className={NOTE}>Загрузка…</p>
      ) : filtered.length === 0 ? (
        !error && <p className={NOTE}>{query ? "Никого не нашли." : "Должников нет."}</p>
      ) : (
        <ul className={LIST}>
          {filtered.map((row) => (
            <li key={row.client_id}>
              <button
                type="button"
                className={ROW}
                onClick={() => onPick(row.client_id, row.client_name || `Клиент #${row.client_id}`)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-semibold">{row.client_name || "—"}</span>
                  <span className="block text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</span>
                </span>
                <CurrencyAmounts
                  className="shrink-0 text-sm font-semibold tabular-nums text-[var(--destructive)]"
                  byCurrency={row.debt_by_currency}
                  fallbackAmount={row.debt_total}
                  fallbackCurrency={row.debt_currency ?? "KZT"}
                />
                <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Шаг 2: какой заказ гасим; недоступные — серые, с причиной. */
export function PosOrderStep({
  clientName,
  detail,
  loading,
  error,
  onRetry,
  onPick,
}: {
  clientName: string;
  detail: ClientDebtDetail | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  onPick: (orderId: number) => void;
}) {
  const orders = detail?.orders ?? [];
  return (
    <section className="flex flex-col gap-3">
      <h2 className="px-1 text-[15px] font-semibold">{clientName}</h2>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {loading && !detail ? (
        <p className={NOTE}>Загрузка…</p>
      ) : orders.length === 0 ? (
        !error && <p className={NOTE}>Долгов нет.</p>
      ) : (
        <ul className={LIST}>
          {orders.map((order) => {
            const block = posOrderBlock(order, detail?.stores ?? []);
            const { max } = wholeTengeLimit(order);
            return (
              <li key={order.id}>
                <button type="button" className={ROW} disabled={block !== null} onClick={() => onPick(order.id)}>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15px] font-semibold">
                      Заказ #{order.id}
                      {order.department_name ? ` · ${order.department_name}` : ""}
                    </span>
                    <span className="block text-xs text-[var(--muted-foreground)]">
                      {block ?? `Можно принять ${formatCurrency(max, order.currency)}`}
                    </span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-[var(--destructive)]">
                    {formatCurrency(remainingOf(order), order.currency)}
                  </span>
                  {!block && <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Шаг 3: сумма на клавиатуре, как в Kaspi POS. */
export function PosAmountStep({
  order,
  amount,
  error,
  busy,
  submitLabel,
  onDigit,
  onErase,
  onFillAll,
  onSubmit,
}: {
  order: Order;
  amount: string;
  error: string;
  busy: boolean;
  submitLabel: string;
  onDigit: (digit: string) => void;
  onErase: () => void;
  onFillAll: () => void;
  onSubmit: () => void;
}) {
  const { max, tiyn } = wholeTengeLimit(order);
  const value = Number(amount || "0");
  return (
    <section className="flex flex-col gap-5">
      <div className="text-center">
        <div className="text-xs text-[var(--muted-foreground)]">
          Заказ #{order.id} · можно принять {formatCurrency(max, order.currency)}
        </div>
        <div aria-live="polite" className="mt-3 text-[44px] font-bold leading-none tracking-tight tabular-nums">
          {formatMoney(value)} ₸
        </div>
        {tiyn > 0 && (
          <div className="mt-2 text-xs text-[var(--muted-foreground)]">
            Тиыны ({formatCurrency(tiyn / 100, "KZT")}) — другим способом
          </div>
        )}
        {value !== max && (
          <Button variant="outline" size="sm" className="mt-3" onClick={onFillAll}>
            Весь остаток
          </Button>
        )}
      </div>
      <Numpad onDigit={onDigit} onBackspace={onErase} disabled={busy} />
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      <Button className="h-12 w-full text-base" disabled={busy || value < 1} onClick={onSubmit}>
        {busy ? "Создаём…" : submitLabel}
      </Button>
    </section>
  );
}

/** «Удаленно»: телефон покупателя для счёта в Kaspi. */
export function PosPhoneStep({
  phone,
  amount,
  error,
  busy,
  onChange,
  onSubmit,
}: {
  phone: string;
  amount: number;
  error: string;
  busy: boolean;
  onChange: (phone: string) => void;
  onSubmit: () => void;
}) {
  const digits = phone.replace(/\D/g, "");
  const valid = digits.length === 10 || digits.length === 11;
  const total = formatCurrency(amount, "KZT");
  return (
    <section className="flex flex-col gap-4">
      <h2 className="px-1 text-[15px] font-semibold">Счёт на оплату в Kaspi</h2>
      <label className="flex flex-col gap-1.5">
        <span className="text-sm text-[var(--muted-foreground)]">Телефон покупателя</span>
        <Input
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          className="h-11 text-base"
          placeholder="8 700 000 00 00"
          value={phone}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <p className="text-xs text-[var(--muted-foreground)]">
        Клиенту придёт счёт в Kaspi на {total}. Долг уменьшится, когда он оплатит.
      </p>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      <Button className="h-12 w-full text-base" disabled={busy || !valid} onClick={onSubmit}>
        {busy ? "Отправляем…" : `Отправить счёт · ${total}`}
      </Button>
    </section>
  );
}
```

- [ ] **Step 6: Итог оплаты** — `frontend/src/components/cashier/mobile/pos/pos-result.tsx`:

```tsx
"use client";
import { CircleCheck, CircleX, ExternalLink } from "lucide-react";
import { QrCodeImage } from "@/components/transactions/qr-code-image";
import { Button } from "@/components/ui/button";
import type { Payment } from "@/lib/types";
import { formatCurrency, formatTime } from "@/lib/utils";
import type { PaymentOutcome } from "./pos-logic";

/** QR на экране, ожидание, «Оплачено» или «больше не действует». */
export function PosResult({
  payment,
  outcome,
  clientName,
  onRetry,
  onNew,
}: {
  payment: Payment;
  outcome: PaymentOutcome;
  clientName: string;
  onRetry: () => void;
  onNew: () => void;
}) {
  const provider = payment.provider;
  const qr = provider?.channel === "qr";
  const amount = formatCurrency(payment.amount, payment.currency ?? "KZT");
  const caption = `${clientName} · заказ #${payment.order}`;

  if (outcome === "paid") {
    return (
      <section className="flex flex-col items-center gap-3 py-8 text-center">
        <CircleCheck className="size-16 text-[var(--success)]" aria-hidden />
        <h2 className="text-2xl font-bold">Оплачено</h2>
        <div className="text-[28px] font-bold tabular-nums">{amount}</div>
        <p className="text-sm text-[var(--muted-foreground)]">{caption}</p>
        <Button className="mt-4 h-12 w-full text-base" onClick={onNew}>
          Новая оплата
        </Button>
      </section>
    );
  }

  if (outcome === "failed") {
    return (
      <section className="flex flex-col items-center gap-3 py-8 text-center">
        <CircleX className="size-16 text-[var(--destructive)]" aria-hidden />
        <h2 className="text-xl font-bold">{qr ? "QR больше не действует" : "Счёт больше не действует"}</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          {caption} · {amount}
        </p>
        <Button className="mt-4 h-12 w-full text-base" onClick={onRetry}>
          {qr ? "Создать новый QR" : "Отправить заново"}
        </Button>
        <Button variant="outline" className="h-11 w-full" onClick={onNew}>
          Новая оплата
        </Button>
      </section>
    );
  }

  return (
    <section className="flex flex-col items-center gap-4 text-center">
      <div>
        <div className="text-[32px] font-bold tabular-nums">{amount}</div>
        <p className="text-sm text-[var(--muted-foreground)]">{caption}</p>
      </div>
      {qr && provider ? (
        <>
          <QrCodeImage key={provider.qr_image_url ?? "no-image"} provider={provider} />
          {provider.qr_token_url && (
            <Button
              variant="outline"
              className="h-11 w-full"
              onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener")}
            >
              <ExternalLink className="size-4" /> Открыть Kaspi
            </Button>
          )}
          {provider.qr_expires_at && (
            <p className="text-xs text-[var(--muted-foreground)]">QR действует до {formatTime(provider.qr_expires_at)}</p>
          )}
        </>
      ) : (
        <p className="text-sm">
          Счёт отправлен{provider?.phone_number ? ` на ${provider.phone_number}` : ""}. Клиент оплатит в Kaspi.
        </p>
      )}
      <p role="status" className="flex items-center gap-2 text-sm font-medium text-[var(--muted-foreground)]">
        <span className="size-2 rounded-full bg-[var(--warning)]" aria-hidden />
        Ожидаем оплату…
      </p>
      {qr && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Не создавайте второй QR на эту сумму — старый ещё можно оплатить.
        </p>
      )}
      <Button variant="outline" className="h-11 w-full" onClick={onNew}>
        {qr ? "Новая оплата" : "Готово"}
      </Button>
    </section>
  );
}
```

- [ ] **Step 7: Кнопка POS** — `frontend/src/components/cashier/mobile/pos/pos-fab.tsx`:

```tsx
"use client";
import { QrCode } from "lucide-react";

/** Круглая кнопка POS по центру снизу — как QR-таб в Kaspi. */
export function PosFab({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center pb-[calc(1rem+env(safe-area-inset-bottom))]">
      <button
        type="button"
        onClick={onClick}
        aria-label="Открыть POS"
        className="pointer-events-auto flex size-16 flex-col items-center justify-center gap-0.5 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] shadow-lg transition-colors hover:bg-[var(--primary)]/90 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
      >
        <QrCode className="size-6" aria-hidden />
        <span className="text-[10px] font-semibold">POS</span>
      </button>
    </div>
  );
}
```

- [ ] **Step 8: Экран POS** — `frontend/src/components/cashier/mobile/pos/pos-screen.tsx`:

```tsx
"use client";
import { History, QrCode, Send } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { cn, formatCurrency } from "@/lib/utils";
import type { CashierModel } from "../../use-cashier";
import { TransactionsScreen } from "../transactions-screen";
import type { PosTab } from "./pos-logic";
import { PosResult } from "./pos-result";
import { PosAmountStep, PosClientStep, PosOrderStep, PosPhoneStep } from "./pos-steps";
import { usePosFlow, type PosFlowApi } from "./use-pos-flow";

const TABS: { key: PosTab; label: string; icon: React.ElementType }[] = [
  { key: "qr", label: "Оплата", icon: QrCode },
  { key: "remote", label: "Удаленно", icon: Send },
  { key: "history", label: "История", icon: History },
];
const TITLES: Record<PosTab, string> = { qr: "POS", remote: "Удаленная оплата", history: "История" };

function PosBody({ model, flow }: { model: CashierModel; flow: PosFlowApi }) {
  const { state, order } = flow;
  if (state.tab === "history") return <TransactionsScreen model={model} />;
  if (state.step === "result" && state.payment && flow.outcome) {
    return (
      <PosResult
        payment={state.payment}
        outcome={flow.outcome}
        clientName={state.clientName}
        onRetry={flow.retry}
        onNew={flow.reset}
      />
    );
  }
  if (state.step === "phone" && order) {
    return (
      <PosPhoneStep
        phone={state.phone}
        amount={Number(state.amount || "0")}
        error={state.error}
        busy={flow.busy}
        onChange={flow.setPhone}
        onSubmit={flow.sendInvoice}
      />
    );
  }
  if (state.step === "amount" && order) {
    const qr = state.flow === "qr";
    return (
      <PosAmountStep
        order={order}
        amount={state.amount}
        error={state.error}
        busy={flow.busy}
        submitLabel={qr ? `Показать QR · ${formatCurrency(Number(state.amount || "0"), "KZT")}` : "Далее"}
        onDigit={flow.digit}
        onErase={flow.erase}
        onFillAll={flow.fillAll}
        onSubmit={qr ? flow.issueQr : flow.toPhone}
      />
    );
  }
  if (state.step !== "client") {
    return (
      <PosOrderStep
        clientName={state.clientName}
        detail={flow.detail.data}
        loading={flow.detail.loading}
        error={flow.detail.error}
        onRetry={() => void flow.detail.reload()}
        onPick={flow.pickOrder}
      />
    );
  }
  return (
    <PosClientStep
      rows={model.debtRows}
      loading={model.debts.loading}
      error={model.debts.error}
      onRetry={() => void model.debts.reload()}
      onPick={flow.pickClient}
    />
  );
}

/** POS кассы на телефоне: оплата долга по Kaspi QR, счёт на телефон и история. */
export function PosScreen({ model, onClose }: { model: CashierModel; onClose: () => void }) {
  const { debts } = model;
  const flow = usePosFlow({ onPaid: () => void debts.reload() });
  const tabs = TABS.filter((tab) => tab.key !== "history" || model.perms.canTransactions);
  return (
    <AppShell
      title={TITLES[flow.state.tab]}
      section="Касса"
      back={{ label: flow.canGoBack ? "Назад" : "Закрыть POS", onClick: flow.canGoBack ? flow.back : onClose }}
    >
      <div className="pb-24">
        <PosBody model={model} flow={flow} />
      </div>
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--border)] bg-[var(--card)] pb-[env(safe-area-inset-bottom)]">
        <div
          role="tablist"
          aria-label="Режим POS"
          className="mx-auto grid max-w-md"
          style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
        >
          {tabs.map((tab) => {
            const active = flow.state.tab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => flow.setTab(tab.key)}
                className={cn(
                  "m-1.5 flex flex-col items-center gap-0.5 rounded-lg py-2 text-[11px] font-medium transition-colors",
                  active ? "bg-[var(--muted)] text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                )}
              >
                <tab.icon className="size-5" aria-hidden />
                {tab.label}
              </button>
            );
          })}
        </div>
      </nav>
    </AppShell>
  );
}
```

- [ ] **Step 9: Подключить к мобильной кассе** — `frontend/src/components/cashier/mobile/mobile-cashier.tsx`:
  - импорты: `import { PosFab } from "./pos/pos-fab";` и `import { PosScreen } from "./pos/pos-screen";`;
  - в `SCREEN_TITLES` добавить `pos: "POS",`;
  - `open` принимает `next: MobileMenuKey | "pos"`;
  - после строки `const rangeError = …` (все хуки уже вызваны) добавить:
    ```tsx
    // POS рисует свой топбар и нижнюю панель; «‹» на первом шаге закрывает его, как подэкран.
    if (view === "pos") return <PosScreen model={model} onClose={back} />;
    const showPos = perms.canCreatePayments;
    ```
  - последними детьми `AppShell` (после строки `TransactionsScreen`) добавить:
    ```tsx
      {showPos && (
        <>
          {/* Место под кнопку POS, чтобы она не закрывала последнюю строку списка. */}
          <div aria-hidden className="h-20" />
          <PosFab onClick={() => open("pos")} />
        </>
      )}
    ```

- [ ] **Step 10: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier src/app/accounting && npx tsc --noEmit && npx eslint src/components/cashier src/app/accounting --max-warnings=0 && npx prettier --write src/components/cashier`
Expected: PASS (включая старые `mobile-cashier.test.tsx` и `page.test.tsx`), чисто. Затем полный `npx vitest run`.

---

### Task 5: Выпуск (контроллер)

- [ ] **Step 1:** `cd backend && .venv/bin/pytest -q` — весь бэкенд зелёный.
- [ ] **Step 2:** `cd frontend && npm run check && npm run build` — чисто; после сборки перезапустить dev-сервер превью (`frontend-local-api`).
- [ ] **Step 3:** один коммит явными путями (без `git add -A`; не трогать чужой `docs/superpowers/specs/2026-09-12-wagon-intake-arch-design.md`), сообщение по подсистемам, в конце `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- [ ] **Step 4:** `git fetch origin && git merge-base --is-ancestor origin/main HEAD` → `git push origin HEAD && git push origin HEAD:main && git branch -f main HEAD`.
- [ ] **Step 5:** `gh run list --branch main --limit 3` → `gh run watch <CI id> --exit-status --interval 30` → затем `Deploy production` так же → `curl -s -o /dev/null -w "%{http_code}" https://asyl-ltd.kz/accounting` (200).

---

## Self-review

**Покрытие спеки:** канал QR кассы + проверки KZT/целых тенге + отказ при сбое провайдера + GET статуса — T1; общие правила долга, клавиатура, QR-картинка — T2; шаги, блокировки заказов, лимит в целых тенге, тиыны, опрос и исходы, «Удаленно», сохранение выбора между табами, сброс после выдачи — T3; кнопка POS по правам, экран, таб-бар, «История», «‹» по шагам/закрытие, `?view=pos` только на телефоне, список должников для поиска — T4; выпуск с пушем и CI — T5.

**Заглушки:** нет; каждое изменение дано кодом или точной правкой.

**Согласованность имён:** `PosFlowApi` (T3) использует T4; `eraseDigit`/`erase`, `fillAll`, `toPhone`, `issueQr`, `sendInvoice`, `canGoBack` — одинаково в хуке и экране; `Numpad.onBackspace` ← `PosAmountStep.onErase`; `QrCodeImage({ provider })` — T2 и T4; `posOrderBlock`/`wholeTengeLimit` — T3 и T4; бэкенд-коды ошибок в спеке и тестах совпадают.
