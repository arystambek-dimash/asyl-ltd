# Мобильная касса (Kaspi-style) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На телефоне (< 768px) раздел «Касса» `/accounting` становится приложением в духе Kaspi Pay (главная-меню → подэкраны, списки, шторки, кольцо отчёта) без потери функций; десктоп визуально не меняется.

**Architecture:** `useIsMobile()` выбирает одно из двух деревьев (`CashierDesktop` / `MobileCashier`) поверх общего хука данных `useCashier` и общих модулей `components/cashier/*`; экран живёт в URL `?view=`. `transactions-section.tsx` разбирается на хук `useTransactions` + общие кнопки действий/детали/модалки, которые использует и десктопная таблица, и мобильный список со шторкой. Бэкенд получает разбивку поступлений по способу оплаты в `/reports/summary/`.

**Tech Stack:** Next 15.5 (app router, `"use client"`), React 19, Tailwind v4 + shadcn-токены, lucide-react 0.474, vitest + testing-library (jsdom), Django/DRF + pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-mobile-cashier-design.md` — план аргументирует от спеки; исполнитель читает обе.

## Global Constraints

- **Коммиты не делать.** Пользователь собирает всё в один коммит и пушит по команде «пушни». Шагов «commit» в плане нет намеренно.
- Десктоп `/accounting` внешне не меняется; единственное поведенческое изменение на десктопе — вкладка синхронизируется с `?view=` через `router.replace(..., { scroll: false })`.
- Граница мобильной раскладки — `(max-width: 767px)` (Tailwind `md`); шторки `Modal variant="sheet"` — тоже `max-md`.
- Дизайн-система прежняя: Inter, токены `--card/--muted/--border/--success/--warning/--destructive/--ring/--muted-foreground`, класс `shadow-card`, светлая и тёмная тема. Без декоративных шрифтов, анимаций сверх `animate-*` из `globals.css`, текстур. Скилл `frontend-design` не подключать; перед `donut-chart.tsx` подключить скилл `dataviz` (Skill tool) и следовать его правилам контраста/подписей.
- Валюты никогда не складываются: крупно основная (`primaryMoneyCurrency`), остальные отдельными строками/через « + ».
- Интерфейсные тексты — по-русски, буква «ё» как в существующем коде.
- Prettier: `printWidth: 120`, `trailingComma: "all"`. После каждого задания: `cd frontend && npx prettier --write <изменённые файлы>`; финально `npm run check` (format:check → lint → typecheck → test) и `npm run build`.
- Frontend-тесты: `cd frontend && npx vitest run <путь>`; backend: `cd backend && .venv/bin/pytest apps/orders/tests/test_reports.py -q`.
- `npm run build` ломает работающий `npm run dev` (оба пишут в `.next`) — после сборки dev перезапустить.
- Все новые файлы с React-хуками начинаются с `"use client";`.

---

## Карта файлов

Новое:
- `frontend/src/lib/use-media-query.ts` — `useMediaQuery`, `useIsMobile`, `MOBILE_MEDIA_QUERY`.
- Группировка по дням: **уже есть** `frontend/src/lib/day-groups.ts` (`groupByDay(items, dateOf, currentDay)` → `{ key, label, items }[]`, подписи «Сегодня / Вчера / 6 сентября 2026») и `frontend/src/lib/use-local-day.ts` (`useLocalDay()`); новых файлов не создавать, формат подписей не менять — он общий с журналом событий и таблицей вагонов.
- `frontend/src/test-utils/next-navigation.ts` — стейтфул-мок `next/navigation` для тестов (`useRouter/usePathname/useSearchParams`, `resetNavigation`, `routerCalls`).
- `frontend/src/components/ui/nav-list.tsx`, `donut-chart.tsx`, `chip.tsx`.
- `frontend/src/components/cashier/view.ts`, `filters.ts`, `totals.ts`, `debt-state.ts`, `department-badge.tsx`, `action-error.tsx`, `use-cashier-queue.ts`, `use-overdue-check.ts`, `order-review-dialogs.tsx`, `restore-payment-dialog.tsx`, `cash-filter-fields.tsx`, `cash-filters-panel.tsx`, `cash-filters-sheet.tsx`, `use-cashier.ts`, `desktop.tsx`.
- `frontend/src/components/cashier/mobile/mobile-cashier.tsx`, `home-screen.tsx`, `confirm-screen.tsx`, `journal-screen.tsx`, `debts-screen.tsx`, `report-screen.tsx`, `report-segments.ts`, `transactions-screen.tsx`.
- `frontend/src/components/transactions/use-transactions.ts`, `transaction-actions.tsx`, `transaction-detail.tsx`, `transaction-modals.tsx`, `paid-method-summary.tsx`.

Изменяется:
- `backend/apps/orders/reports.py`, `backend/apps/orders/tests/test_reports.py`.
- `frontend/src/lib/types.ts`, `frontend/src/app/globals.css`.
- `frontend/src/components/ui/modal.tsx` (+ `modal.test.tsx`), `frontend/src/components/layout/topbar.tsx`, `app-shell.tsx`.
- `frontend/src/app/accounting/page.tsx` (становится тонким), `page.test.tsx` (мок навигации).
- `frontend/src/components/transactions-section.tsx` (десктопная композиция над `components/transactions/*`).

---

### Task 1: Бэкенд — разбивка поступлений по способу оплаты

**Files:**
- Modify: `backend/apps/orders/reports.py:52-110` (группировка событий), `:281-458` (`summary_report`)
- Test: `backend/apps/orders/tests/test_reports.py`

**Interfaces:**
- Consumes: существующие `_payment_events_by_day`, `_refund_events_by_day`, `summary_report`.
- Produces: в ответе `/reports/summary/` (оба режима) `income.by_method_by_currency: {валюта: {способ: "нетто"}}`, `income.payments_by_method: {способ: int}`, `departments[].payments: int`.

- [ ] **Step 1: Написать падающий тест** — добавить в конец `backend/apps/orders/tests/test_reports.py`:

```python
def test_income_by_method_is_net_of_refunds(auth_client, boss):
    """Возврат уменьшает способ исходной оплаты, а не способ выдачи денег."""
    order = _shipped_order(_client(), _product(), qty=10, price="1000")
    _confirmed_payment(order, 3000, method="cash")
    kaspi = _confirmed_payment(order, 1000, method="kaspi")
    PaymentRefund.objects.create(
        payment=kaspi,
        amount="200.00",
        method="apipay",
        status="completed",
        reason="Возврат по счёту",
        completed_at=timezone.now(),
    )

    data = auth_client(boss).get(URL, {"section": "income"}).json()

    assert data["income"]["by_method_by_currency"] == {
        "KZT": {"cash": "3000.00", "kaspi": "800.00"},
    }
    assert data["income"]["payments_by_method"] == {"cash": 1, "kaspi": 1}
    assert data["income"]["cashless"] == "800.00"
    assert data["income"]["total"] == "3800.00"
    department = next(row for row in data["departments"] if row["code"] == "main")
    assert department["payments"] == 2
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && .venv/bin/pytest apps/orders/tests/test_reports.py -q -k by_method`
Expected: FAIL — `KeyError: 'by_method_by_currency'`.

- [ ] **Step 3: Группировать события по способу оплаты** — в `_payment_events_by_day` заменить строку `.values("day", "order__currency", "order__department")` на:

```python
        .values("day", "order__currency", "order__department", "method")
```

и в `_refund_events_by_day` строку `.values("day", "payment__order__currency", "payment__order__department")` на:

```python
        .values(
            "day",
            "payment__order__currency",
            "payment__order__department",
            "payment__method",
        )
```

- [ ] **Step 4: Считать нетто по способу и число оплат отдела** — в `summary_report`:

В `department_row` добавить счётчик оплат:

```python
    def department_row(code):
        return departments.setdefault(
            code,
            {
                "code": code,
                "orders": 0,
                "payments": 0,
                "sales_by_currency": defaultdict(lambda: _ZERO),
                "received_by_currency": defaultdict(lambda: _ZERO),
                "refunded_by_currency": defaultdict(lambda: _ZERO),
            },
        )
```

Заменить цикл по оплатам (от `income_by_currency = defaultdict(_income_currency_row)` до `payments_total += event["payments"]` включительно) на:

```python
    income_by_currency = defaultdict(_income_currency_row)
    # {валюта: {способ: нетто}} — возврат вычитается из способа исходной
    # оплаты, как paid_by_method в транзакциях; нал/безнал считаются как раньше.
    income_by_method = defaultdict(lambda: defaultdict(lambda: _ZERO))
    payments_by_method = defaultdict(int)
    payments_total = 0
    for event in _payment_events_by_day(orders_qs, date_from, date_to):
        row = day_row(event["day"])
        currency = event["order__currency"] or DEFAULT_CURRENCY
        gross = event["gross_cash"] + event["gross_cashless"]
        department = department_row(event["order__department"])
        department["received_by_currency"][currency] += gross
        department["payments"] += event["payments"]
        row["gross_cash_by_currency"][currency] += event["gross_cash"]
        row["gross_cashless_by_currency"][currency] += event["gross_cashless"]
        row["payments"] += event["payments"]
        income_by_currency[currency]["gross_cash"] += event["gross_cash"]
        income_by_currency[currency]["gross_cashless"] += event["gross_cashless"]
        income_by_method[currency][event["method"]] += gross
        payments_by_method[event["method"]] += event["payments"]
        payments_total += event["payments"]
```

В цикле по возвратам после строки `refunds_total += event["refunds"]` добавить:

```python
        income_by_method[currency][event["payment__method"]] -= (
            event["refund_cash"] + event["refund_cashless"]
        )
```

В словарь `income` после ключа `"cashless_by_currency": ...` добавить:

```python
        "by_method_by_currency": {
            currency: as_money_strings(dict(methods))
            for currency, methods in income_by_method.items()
        },
        "payments_by_method": dict(payments_by_method),
```

В `department_rows.append({...})` после `"orders": ...` добавить:

```python
                "payments": values["payments"],
```

- [ ] **Step 5: Прогнать тесты отчётов**

Run: `cd backend && .venv/bin/pytest apps/orders/tests/test_reports.py -q`
Expected: все PASS (старые проверки `cash/cashless/total/days` не меняются: группировка по способу лишь дробит строки, суммы те же).

---

### Task 2: Библиотека — типы, `useIsMobile`, мок навигации

Группировка по дням в задаче НЕ создаётся: в репо уже есть `frontend/src/lib/day-groups.ts` — `groupByDay(items, dateOf: (item) => Date | null | undefined, currentDay: string)` возвращает `DayGroup<T>[] = { key: string; label: string; items: T[] }[]` с подписями «Сегодня», «Вчера», «6 сентября 2026» (`formatDayLabel`), а `frontend/src/lib/use-local-day.ts` даёт реактивный `useLocalDay(): string` (локальный день, обновляется в полночь). Мобильные экраны (Tasks 9, 11) используют их как есть. Файл `day-groups.ts` не трогать.

**Files:**
- Modify: `frontend/src/lib/types.ts:145-174`
- Create: `frontend/src/lib/use-media-query.ts`, `frontend/src/lib/use-media-query.test.tsx`, `frontend/src/test-utils/next-navigation.ts`

**Interfaces:**
- Produces: `useIsMobile(): boolean`, `useMediaQuery(query: string): boolean`, `MOBILE_MEDIA_QUERY`; тест-мок `resetNavigation(url)`, `routerCalls`, `currentUrl()`; типы `DepartmentReport.payments?`, `income.by_method_by_currency?`, `income.payments_by_method?`.
- Consumes (существующее): `groupByDay`/`DayGroup` из `@/lib/day-groups`, `useLocalDay` из `@/lib/use-local-day`.

- [ ] **Step 1: Типы** — в `frontend/src/lib/types.ts` в `DepartmentReport` после `net_by_currency` добавить:

```ts
  /** Число подтверждённых оплат отдела; нет у старого бэкенда во время раскатки. */
  payments?: number;
```

в `ReportSummary.income` после `refunded_by_currency` добавить:

```ts
    /** {валюта: {способ: нетто}} — нет у старого бэкенда во время раскатки. */
    by_method_by_currency?: Record<string, Record<string, string>>;
    payments_by_method?: Record<string, number>;
```

- [ ] **Step 2: Падающий тест хука** — создать `frontend/src/lib/use-media-query.test.tsx`:

```tsx
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useIsMobile } from "./use-media-query";

type Listener = () => void;

function installMatchMedia(matches: boolean) {
  const listeners = new Set<Listener>();
  const media = {
    matches,
    media: "",
    addEventListener: (_event: string, listener: Listener) => listeners.add(listener),
    removeEventListener: (_event: string, listener: Listener) => listeners.delete(listener),
  };
  Object.defineProperty(window, "matchMedia", { configurable: true, writable: true, value: vi.fn(() => media) });
  return {
    resize(next: boolean) {
      media.matches = next;
      listeners.forEach((listener) => listener());
    },
  };
}

afterEach(() => {
  Reflect.deleteProperty(window, "matchMedia");
});

it("is false where matchMedia is unavailable", () => {
  const { result } = renderHook(() => useIsMobile());
  expect(result.current).toBe(false);
});

it("follows the media query", () => {
  const viewport = installMatchMedia(true);
  const { result } = renderHook(() => useIsMobile());
  expect(result.current).toBe(true);
  act(() => viewport.resize(false));
  expect(result.current).toBe(false);
});
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd frontend && npx vitest run src/lib/use-media-query.test.tsx`
Expected: FAIL — модуль не найден.

- [ ] **Step 4: Реализация** — создать `frontend/src/lib/use-media-query.ts`:

```ts
"use client";
import { useCallback, useSyncExternalStore } from "react";

/** Ширина, с которой касса переключается на мобильную раскладку (граница `md`). */
export const MOBILE_MEDIA_QUERY = "(max-width: 767px)";

function mediaList(query: string): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(query);
}

/** Реактивный `matchMedia`. На сервере и в jsdom без matchMedia — всегда false. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = mediaList(query);
      if (!media) return () => {};
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => mediaList(query)?.matches ?? false,
    () => false,
  );
}

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_MEDIA_QUERY);
}
```

- [ ] **Step 5: Мок навигации для тестов** — создать `frontend/src/test-utils/next-navigation.ts`:

```ts
import { useSyncExternalStore } from "react";

/**
 * Стейтфул-мок `next/navigation` для vitest: push/replace/back меняют URL,
 * `usePathname`/`useSearchParams` перерисовывают подписчиков. Подключать так:
 * `vi.mock("next/navigation", () => import("@/test-utils/next-navigation"))`.
 */
const state = { url: "/", stack: ["/"], listeners: new Set<() => void>() };

export const routerCalls = { push: [] as string[], replace: [] as string[], back: 0 };

function notify() {
  state.listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  state.listeners.add(listener);
  return () => state.listeners.delete(listener);
}

export function resetNavigation(url = "/") {
  state.url = url;
  state.stack = [url];
  routerCalls.push = [];
  routerCalls.replace = [];
  routerCalls.back = 0;
  notify();
}

export function currentUrl() {
  return state.url;
}

export function useRouter() {
  return {
    push: (url: string) => {
      routerCalls.push.push(url);
      state.stack.push(url);
      state.url = url;
      notify();
    },
    replace: (url: string) => {
      routerCalls.replace.push(url);
      state.stack[state.stack.length - 1] = url;
      state.url = url;
      notify();
    },
    back: () => {
      routerCalls.back += 1;
      if (state.stack.length > 1) state.stack.pop();
      state.url = state.stack[state.stack.length - 1];
      notify();
    },
    prefetch: () => {},
    refresh: () => {},
  };
}

const pathnameOf = () => state.url.split("?")[0];
const searchOf = () => state.url.split("?")[1] ?? "";

export function usePathname() {
  return useSyncExternalStore(subscribe, pathnameOf, pathnameOf);
}

export function useSearchParams() {
  const search = useSyncExternalStore(subscribe, searchOf, searchOf);
  return new URLSearchParams(search);
}
```

- [ ] **Step 6: Тесты зелёные, типы сходятся**

Run: `cd frontend && npx vitest run src/lib/use-media-query.test.tsx && npx tsc --noEmit`
Expected: PASS, без ошибок типов.

---

### Task 3: Оболочка — `Modal variant="sheet"`, кнопка «назад» и слот справа в топбаре

**Files:**
- Modify: `frontend/src/components/ui/modal.tsx:80-219`, `frontend/src/components/ui/modal.test.tsx`, `frontend/src/app/globals.css:187-190`, `frontend/src/components/layout/topbar.tsx:85-200`, `frontend/src/components/layout/app-shell.tsx:14-30,114`
- Create: `frontend/src/components/layout/topbar.test.tsx`

**Interfaces:**
- Produces: `Modal` проп `variant?: "dialog" | "sheet"`; `Topbar`/`AppShell` пропы `back?: TopbarBack` (`{ label: string; onClick: () => void }`) и `trailing?: ReactNode`; экспорт `type TopbarBack` из `topbar.tsx`.

- [ ] **Step 1: Падающие тесты** — в конец `frontend/src/components/ui/modal.test.tsx` добавить:

```tsx
describe("Modal sheet variant", () => {
  it("marks the dialog as a bottom sheet", () => {
    render(
      <Modal open onClose={() => {}} title="Фильтры" variant="sheet">
        тело
      </Modal>,
    );
    expect(screen.getByRole("dialog", { name: "Фильтры" })).toHaveClass("animate-sheet-content");
  });
});
```

создать `frontend/src/components/layout/topbar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Topbar } from "./topbar";
import type { Me } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ logout: vi.fn() }) }));
vi.mock("@/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/components/onboarding-tour", () => ({ TOUR_START_EVENT: "tour" }));

const me: Me = {
  id: 1,
  username: "kassa",
  is_client: false,
  is_superuser: false,
  permissions: [],
  position: "Касса",
  client_id: null,
  sales_department: null,
};

it("shows the menu button by default", () => {
  render(<Topbar me={me} title="Касса" />);
  expect(screen.getByRole("button", { name: "Меню" })).toBeInTheDocument();
});

it("replaces the menu with a back button and renders the trailing slot", async () => {
  const onClick = vi.fn();
  render(<Topbar me={me} title="Заявки" back={{ label: "Назад в кассу", onClick }} trailing={<span>слот</span>} />);
  expect(screen.queryByRole("button", { name: "Меню" })).not.toBeInTheDocument();
  expect(screen.getByText("слот")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(onClick).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/ui/modal.test.tsx src/components/layout/topbar.test.tsx`
Expected: FAIL (нет класса `animate-sheet-content`; кнопка «Меню» остаётся, «Назад в кассу» не найдена).

- [ ] **Step 3: Анимация шторки** — в `frontend/src/app/globals.css` сразу после блока `.animate-modal-content { ... }` добавить:

```css
@keyframes sheet-content {
  from {
    opacity: 0;
    transform: translateY(24px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Шторка выезжает снизу только на телефоне; на десктопе тот же диалог, что и всегда. */
.animate-sheet-content {
  animation: sheet-content 0.24s cubic-bezier(0.16, 1, 0.3, 1) both;
}
@media (min-width: 768px) {
  .animate-sheet-content {
    animation: modal-content 0.2s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
}
```

- [ ] **Step 4: Вариант `sheet` в Modal** — в `frontend/src/components/ui/modal.tsx` добавить проп и классы:

В сигнатуре `Modal` после `mobileFullscreen = false,` добавить `variant = "dialog",`, а в типе пропов после `mobileFullscreen?: boolean;` — `/** "sheet": на телефоне шторка снизу, на десктопе обычный диалог. */ variant?: "dialog" | "sheet";`.

Перед `return createPortal(` добавить `const sheet = variant === "sheet";` и заменить разметку обёртки и панели:

```tsx
    <div
      className={cn(
        "fixed inset-0 z-[100] flex items-center justify-center p-4",
        mobileFullscreen && "max-sm:p-0",
        sheet && "max-md:items-end max-md:p-0",
      )}
      onKeyDown={trapFocus}
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-[1px] animate-modal-backdrop"
        onClick={() => onCloseRef.current()}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative z-10 flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border bg-[var(--card)] shadow-2xl",
          sheet ? "animate-sheet-content" : "animate-modal-content",
          sheet &&
            "max-md:max-h-[92dvh] max-md:max-w-none max-md:rounded-b-none max-md:rounded-t-2xl max-md:border-x-0 max-md:border-b-0 max-md:pb-[env(safe-area-inset-bottom)]",
          mobileFullscreen && "max-sm:h-[100dvh] max-sm:max-h-[100dvh] max-sm:rounded-none max-sm:border-0",
          className,
        )}
      >
        {sheet && <div aria-hidden className="mx-auto mt-2 h-1 w-9 shrink-0 rounded-full bg-[var(--border)] md:hidden" />}
```

Остальное (шапка, тело, футер) без изменений.

- [ ] **Step 5: `back` и `trailing` в Topbar** — в `frontend/src/components/layout/topbar.tsx`:

Импорт: `import { LogOut, Sun, Moon, Monitor, Menu, CircleHelp, ChevronLeft } from "lucide-react";`

Перед `export function Topbar(` добавить:

```tsx
/** Кнопка «назад» вместо «☰» на подэкранах мобильных разделов. */
export interface TopbarBack {
  label: string;
  onClick: () => void;
}
```

Сигнатуру дополнить пропами `back` и `trailing`:

```tsx
export function Topbar({
  me,
  title,
  section,
  tabs,
  actions,
  onMenu,
  back,
  trailing,
}: {
  me: Me;
  title: string;
  section?: string;
  tabs?: ReactNode;
  actions?: ReactNode;
  onMenu?: () => void;
  back?: TopbarBack;
  /** Иконки справа от заголовка (перед темой и профилем), например фильтры экрана. */
  trailing?: ReactNode;
}) {
```

Кнопку меню заменить условием:

```tsx
        {back ? (
          <button
            type="button"
            onClick={back.onClick}
            className="-ml-1 flex size-9 shrink-0 items-center justify-center rounded-md text-[var(--foreground)] hover:bg-[var(--secondary)] lg:hidden"
            aria-label={back.label}
          >
            <ChevronLeft className="size-5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={onMenu}
            className="-ml-1 flex size-9 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:bg-[var(--secondary)] lg:hidden"
            aria-label="Меню"
          >
            <Menu className="size-5" />
          </button>
        )}
```

В правом блоке `<div className="flex shrink-0 items-center gap-2 sm:gap-3">` первым дочерним элементом добавить `{trailing}`.

- [ ] **Step 6: Прокинуть через AppShell** — в `frontend/src/components/layout/app-shell.tsx`: импорт `import { Sidebar } from "./sidebar"; import { Topbar, type TopbarBack } from "./topbar";`; в пропы добавить `back?: TopbarBack; trailing?: React.ReactNode;` (и в деструктуризацию `back, trailing`); вызов топбара:

```tsx
        <Topbar
          me={me}
          title={title}
          section={section}
          tabs={tabs}
          actions={actions}
          onMenu={openNav}
          back={back}
          trailing={trailing}
        />
```

- [ ] **Step 7: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/ui/modal.test.tsx src/components/layout/topbar.test.tsx src/components/layout/app-shell.test.tsx`
Expected: PASS.

---

### Task 4: UI-примитивы — `NavList`, `DonutChart`, `Chip`

**Files:**
- Create: `frontend/src/components/ui/nav-list.tsx`, `nav-list.test.tsx`, `frontend/src/components/ui/donut-chart.tsx`, `donut-chart.test.tsx`, `frontend/src/components/ui/chip.tsx`

**Interfaces:**
- Produces: `NavList({ items: NavListItem[]; label: string; className? })`, `NavListItem = { key; icon: React.ElementType; title; subtitle?; value?; href?; onSelect? }`; `DonutChart({ segments: DonutSegment[]; centerValue: string; centerLabel?; emptyLabel?; size?; thickness?; className? })`, `DonutSegment = { key; label; value: number; color: string }`; `Chip({ active; onClick; children; className? })`.

- [ ] **Step 1: Падающие тесты** — создать `frontend/src/components/ui/nav-list.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Users } from "lucide-react";
import { expect, it, vi } from "vitest";
import { NavList } from "./nav-list";

it("renders links and buttons with their titles", async () => {
  const onSelect = vi.fn();
  render(
    <NavList
      label="Разделы"
      items={[
        { key: "debts", icon: Users, title: "Долги клиентов", subtitle: "12 клиентов", href: "/accounting?view=debts" },
        { key: "journal", icon: Users, title: "Журнал", value: "3", onSelect },
      ]}
    />,
  );
  expect(screen.getByRole("link", { name: /Долги клиентов/ })).toHaveAttribute("href", "/accounting?view=debts");
  expect(screen.getByText("12 клиентов")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Журнал/ }));
  expect(onSelect).toHaveBeenCalledTimes(1);
});
```

создать `frontend/src/components/ui/donut-chart.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { DonutChart } from "./donut-chart";

const segments = [
  { key: "cash", label: "Наличные", value: 300, color: "green" },
  { key: "kaspi", label: "QR", value: 100, color: "blue" },
  { key: "card", label: "Карта", value: 0, color: "gray" },
];

it("draws one arc per positive segment and describes the shares", () => {
  const { container } = render(<DonutChart segments={segments} centerValue="400 ₸" centerLabel="2 оплаты" />);
  const arcs = container.querySelectorAll("circle[stroke-dasharray]");
  expect(arcs).toHaveLength(2);
  expect(arcs[0]).toHaveAttribute("stroke-dasharray", "75 25");
  expect(arcs[1]).toHaveAttribute("stroke-dashoffset", "-75");
  expect(screen.getByRole("img", { name: "400 ₸, 2 оплаты. Наличные: 75%, QR: 25%" })).toBeInTheDocument();
  expect(screen.getByText("400 ₸")).toBeInTheDocument();
});

it("shows only the track when there is nothing to split", () => {
  const { container } = render(<DonutChart segments={[]} centerValue="0 ₸" emptyLabel="Пусто" />);
  expect(container.querySelectorAll("circle")).toHaveLength(1);
  expect(screen.getByRole("img", { name: "0 ₸. Пусто" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/ui/nav-list.test.tsx src/components/ui/donut-chart.test.tsx`
Expected: FAIL — модули не найдены.

- [ ] **Step 3: `NavList`** — создать `frontend/src/components/ui/nav-list.tsx`:

```tsx
"use client";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface NavListItem {
  key: string;
  icon: React.ElementType;
  title: string;
  subtitle?: string;
  /** Значение справа (сумма, счётчик) — табличными цифрами. */
  value?: string;
  href?: string;
  onSelect?: () => void;
}

const ROW_CLASS =
  "flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60 focus-visible:bg-[var(--muted)]/60 focus-visible:outline-none";

/** Список-меню как в мобильных банках: иконка, название, подзаголовок, «›». */
export function NavList({ items, label, className }: { items: NavListItem[]; label: string; className?: string }) {
  return (
    <nav
      aria-label={label}
      className={cn("overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card", className)}
    >
      <ul className="divide-y divide-[var(--border)]">
        {items.map((item) => {
          const content = (
            <>
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--foreground)]">
                <item.icon className="size-5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold leading-tight">{item.title}</span>
                {item.subtitle && (
                  <span className="mt-0.5 line-clamp-2 block text-[13px] text-[var(--muted-foreground)]">
                    {item.subtitle}
                  </span>
                )}
              </span>
              {item.value && <span className="shrink-0 text-[15px] font-semibold tabular-nums">{item.value}</span>}
              <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
            </>
          );
          return (
            <li key={item.key}>
              {item.href ? (
                <Link href={item.href} className={ROW_CLASS}>
                  {content}
                </Link>
              ) : (
                <button type="button" onClick={item.onSelect} className={ROW_CLASS}>
                  {content}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
```

- [ ] **Step 4: `DonutChart`** — сначала подключить скилл `dataviz` (Skill tool) и сверить с ним подписи/контраст; затем создать `frontend/src/components/ui/donut-chart.tsx`:

```tsx
import { cn } from "@/lib/utils";

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /** Любой CSS-цвет; для способов оплаты — токены темы, для отделов — их цвет. */
  color: string;
}

/**
 * Кольцевая диаграмма долей без зависимостей: SVG-дуги через pathLength=100,
 * значение и подпись в центре, текстовое описание долей для скринридера.
 */
export function DonutChart({
  segments,
  centerValue,
  centerLabel,
  emptyLabel = "Нет данных",
  size = 208,
  thickness = 22,
  className,
}: {
  segments: DonutSegment[];
  centerValue: string;
  centerLabel?: string;
  emptyLabel?: string;
  size?: number;
  thickness?: number;
  className?: string;
}) {
  const positive = segments.filter((segment) => segment.value > 0);
  const total = positive.reduce((sum, segment) => sum + segment.value, 0);
  const radius = (size - thickness) / 2;
  const center = size / 2;
  let offset = 0;
  const arcs = positive.map((segment) => {
    const share = (segment.value / total) * 100;
    const arc = { ...segment, share, start: offset };
    offset += share;
    return arc;
  });
  const description =
    total > 0 ? arcs.map((arc) => `${arc.label}: ${Math.round(arc.share)}%`).join(", ") : emptyLabel;

  return (
    <div className={cn("relative mx-auto", className)} style={{ width: size, height: size }}>
      <svg
        role="img"
        aria-label={`${centerValue}${centerLabel ? `, ${centerLabel}` : ""}. ${description}`}
        viewBox={`0 0 ${size} ${size}`}
        className="size-full -rotate-90"
      >
        <circle cx={center} cy={center} r={radius} fill="none" stroke="var(--muted)" strokeWidth={thickness} />
        {arcs.map((arc) => (
          <circle
            key={arc.key}
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke={arc.color}
            strokeWidth={thickness}
            pathLength={100}
            strokeDasharray={`${arc.share} ${100 - arc.share}`}
            strokeDashoffset={-arc.start}
          />
        ))}
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-6 text-center">
        <span className="text-[22px] font-bold leading-none tracking-tight tabular-nums">{centerValue}</span>
        {centerLabel && <span className="mt-1.5 text-xs text-[var(--muted-foreground)]">{centerLabel}</span>}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: `Chip`** — создать `frontend/src/components/ui/chip.tsx` (стиль — точная копия пилюль статус-фильтра транзакций, чтобы десктоп не изменился):

```tsx
"use client";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Пилюля-переключатель фильтра (статус, период): `aria-pressed` отражает выбор. */
export function Chip({
  active,
  onClick,
  children,
  className,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-[var(--foreground)] bg-[var(--muted)]"
          : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--foreground)]/40",
        className,
      )}
    >
      {children}
    </button>
  );
}
```

- [ ] **Step 6: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/ui/nav-list.test.tsx src/components/ui/donut-chart.test.tsx`
Expected: PASS.

---

### Task 5: Чистые модули кассы — экраны, фильтры, итоги

**Files:**
- Create: `frontend/src/components/cashier/view.ts`, `view.test.ts`, `filters.ts`, `filters.test.ts`, `totals.ts`, `totals.test.ts`, `debt-state.ts`, `department-badge.tsx`, `action-error.tsx`

**Interfaces:**
- Consumes: `can(me, code)` из `@/lib/can`; `toLocalIsoDate`, `sumDebtByCurrency`, `sumMoneyByCurrency` из `@/lib/utils`; `amountForCurrency`, `otherCurrencyAmounts`, `primaryMoneyCurrency` из `@/lib/currency-map`.
- Produces:
  - `view.ts`: `CashView`, `MobileMenuKey`, `CashierPerms`, `cashierPerms(me)`, `viewAllowed(view, perms)`, `mobileMenu(perms)`, `defaultView(perms, mobile)`, `resolveView(raw, perms, mobile)`, `CASHIER_ENTRY_PERMS`.
  - `filters.ts`: `CashFilters`, `EMPTY_CASH_FILTERS`, `FilterScreen`, `CashFiltersByScreen`, `PeriodPreset`, `PERIOD_PRESETS`, `periodRange`, `periodPresetOf`, `initialFilters`, `filterScreenFor`, `apiUrl`, `filtersAreValid`, `activeFilterCount`, `scopeParams`.
  - `totals.ts`: `IncomeSummary`, `QueueTotal`, `IncomeTotals`, `incomeTotals`, `QueueTotals`, `queueTotals`, `DebtTotals`, `debtTotals`.
  - `debt-state.ts`: `debtPaymentState(row)`, `matchesDebtQuery(row, query)`; `department-badge.tsx`: `DepartmentBadge`; `action-error.tsx`: `ActionError`.

- [ ] **Step 1: Падающие тесты** — создать `frontend/src/components/cashier/view.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Me } from "@/lib/types";
import { cashierPerms, defaultView, mobileMenu, resolveView } from "./view";

function me(permissions: string[], is_superuser = false): Me {
  return { id: 1, username: "u", is_client: false, is_superuser, permissions, position: null, client_id: null, sales_department: null };
}
const all = cashierPerms(me([], true));
const viewer = cashierPerms(me(["payments.view"]));

describe("resolveView", () => {
  it("opens the mobile home and the desktop overview by default", () => {
    expect(resolveView(null, all, true)).toBe("home");
    expect(resolveView(null, all, false)).toBe("overview");
  });
  it("maps values foreign to the layout", () => {
    expect(resolveView("overview", all, true)).toBe("home");
    expect(resolveView("report", all, false)).toBe("overview");
    expect(resolveView("debts", all, false)).toBe("overview");
    expect(resolveView("home", all, false)).toBe("overview");
  });
  it("falls back when the screen is not allowed or unknown", () => {
    expect(resolveView("confirm", viewer, false)).toBe("transactions");
    expect(resolveView("garbage", all, true)).toBe("home");
  });
  it("skips the home screen when only one section is available", () => {
    expect(mobileMenu(viewer)).toEqual(["transactions"]);
    expect(defaultView(viewer, true)).toBe("transactions");
    expect(resolveView("home", viewer, true)).toBe("transactions");
  });
});

describe("cashierPerms", () => {
  it("derives combined permissions", () => {
    const perms = cashierPerms(me(["payments.create", "orders.view"]));
    expect(perms.canDebtEntry).toBe(true);
    expect(perms.canReviewOrders).toBe(false);
    expect(mobileMenu(perms)).toEqual(["debts"]);
    expect(mobileMenu(all)).toEqual(["confirm", "debts", "transactions", "journal", "report"]);
  });
});
```

создать `frontend/src/components/cashier/filters.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  EMPTY_CASH_FILTERS,
  activeFilterCount,
  filterScreenFor,
  initialFilters,
  periodPresetOf,
  periodRange,
} from "./filters";

const now = new Date(2026, 8, 12);

describe("periodRange", () => {
  it("builds presets from today", () => {
    expect(periodRange("today", now)).toEqual({ dateFrom: "2026-09-12", dateTo: "2026-09-12" });
    expect(periodRange("week", now)).toEqual({ dateFrom: "2026-09-06", dateTo: "2026-09-12" });
    expect(periodRange("month", now)).toEqual({ dateFrom: "2026-09-01", dateTo: "2026-09-12" });
    expect(periodRange("all", now)).toEqual({ dateFrom: "", dateTo: "" });
  });
  it("recognises the active preset", () => {
    expect(periodPresetOf({ dateFrom: "2026-09-12", dateTo: "2026-09-12" }, now)).toBe("today");
    expect(periodPresetOf({ dateFrom: "", dateTo: "" }, now)).toBe("all");
    expect(periodPresetOf({ dateFrom: "2026-09-02", dateTo: "2026-09-12" }, now)).toBe("custom");
  });
  it("starts the mobile report at today only", () => {
    const filters = initialFilters(now);
    expect(filters.report).toMatchObject({ dateFrom: "2026-09-12", dateTo: "2026-09-12" });
    expect(filters.overview).toEqual(EMPTY_CASH_FILTERS);
  });
});

describe("filterScreenFor", () => {
  it("hides the panel where a screen has no filters", () => {
    expect(filterScreenFor("overview", false)).toBe("overview");
    expect(filterScreenFor("overview", true)).toBeNull();
    expect(filterScreenFor("report", true)).toBe("report");
    expect(filterScreenFor("report", false)).toBeNull();
    expect(filterScreenFor("confirm", true)).toBe("confirm");
    expect(filterScreenFor("transactions", false)).toBeNull();
    expect(filterScreenFor("home", true)).toBeNull();
  });
});

describe("activeFilterCount", () => {
  it("counts only the requested groups", () => {
    const filters = { ...EMPTY_CASH_FILTERS, dateFrom: "2026-09-01", department: "main", remainingMin: "10" };
    expect(activeFilterCount(filters)).toBe(2);
    expect(activeFilterCount(filters, { remaining: true })).toBe(3);
    expect(activeFilterCount(filters, { dates: false })).toBe(1);
  });
});
```

создать `frontend/src/components/cashier/totals.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ClientDebt } from "@/lib/types";
import { debtTotals, incomeTotals, queueTotals } from "./totals";

const debtRow: ClientDebt = {
  client_id: 1,
  client_name: "Клиент",
  client_phone: "",
  debt_total: "100",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "100" },
  orders_count: 1,
  unpaid_count: 1,
  partial_count: 0,
  stores_count: 0,
  overdue_count: 0,
};

describe("queueTotals", () => {
  it("sums grouped rows and counts payments, not groups", () => {
    const totals = queueTotals([
      { currency: "KZT", method: "cash", amount: "100", count: 2 },
      { currency: "KZT", method: "kaspi", amount: "50", count: 1 },
      { currency: "USD", method: "cash", amount: "5", count: 1 },
    ]);
    expect(totals).toMatchObject({ currency: "KZT", total: 150, cash: 100, count: 4, other: [["USD", 5]] });
  });
  it("treats rows without a count as single payments", () => {
    expect(queueTotals([{ currency: "KZT", method: "cash", amount: "100" }]).count).toBe(1);
  });
});

describe("debtTotals", () => {
  it("never adds currencies together", () => {
    const totals = debtTotals([debtRow, { ...debtRow, client_id: 2, debt_by_currency: { USD: "5" }, overdue_count: 1 }]);
    expect(totals).toMatchObject({ currency: "KZT", total: 100, other: [["USD", 5]], clients: 2, overdue: 1 });
  });
});

describe("incomeTotals", () => {
  it("reads the primary currency and keeps the rest separate", () => {
    const totals = incomeTotals({
      from: null,
      to: null,
      income: {
        total: "90",
        cash: "40",
        cashless: "50",
        gross: "100",
        refunded: "10",
        payments: 3,
        refunds: 1,
        currency: "KZT",
        by_currency: { KZT: "90", USD: "5" },
        cash_by_currency: { KZT: "40" },
        cashless_by_currency: { KZT: "50" },
        gross_by_currency: { KZT: "100", USD: "5" },
        refunded_by_currency: { KZT: "10" },
      },
    });
    expect(totals).toMatchObject({
      currency: "KZT",
      total: 90,
      cash: 40,
      cashless: 50,
      gross: 100,
      refunded: 10,
      payments: 3,
      otherCurrencies: [["USD", 5]],
    });
    expect(totals.grossFor("USD")).toBe(5);
  });
  it("is empty without data", () => {
    expect(incomeTotals(null)).toMatchObject({ currency: "KZT", total: 0, payments: 0, otherCurrencies: [] });
  });
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/cashier`
Expected: FAIL — модули не найдены.

- [ ] **Step 3: `view.ts`** — создать `frontend/src/components/cashier/view.ts`:

```ts
import { can } from "@/lib/can";
import type { Me } from "@/lib/types";

/** Экран кассы: десктоп знает overview/confirm/journal/transactions, телефон — home/report/debts/confirm/journal/transactions. */
export type CashView = "home" | "overview" | "report" | "debts" | "confirm" | "journal" | "transactions";
export type MobileMenuKey = Exclude<CashView, "home" | "overview">;

/** Права раздела — RequirePerm пускает при любом из них. */
export const CASHIER_ENTRY_PERMS = ["payments.confirm", "payments.create", "reports.view", "payments.view"];

export interface CashierPerms {
  canPayments: boolean;
  canCreatePayments: boolean;
  canReports: boolean;
  canDebtEntry: boolean;
  canTransactions: boolean;
  canViewOrders: boolean;
  canReviewOrders: boolean;
  canViewClients: boolean;
  canCheckOverdue: boolean;
}

export function cashierPerms(me: Me | null): CashierPerms {
  const canPayments = can(me, "payments.confirm");
  const canCreatePayments = can(me, "payments.create");
  const canReports = can(me, "reports.view");
  const canViewOrders = can(me, "orders.view");
  return {
    canPayments,
    canCreatePayments,
    canReports,
    canDebtEntry: canReports || canCreatePayments,
    canTransactions: can(me, "payments.view"),
    canViewOrders,
    canReviewOrders: canViewOrders && can(me, "orders.confirm"),
    canViewClients: can(me, "clients.view"),
    canCheckOverdue: can(me, "clients.edit"),
  };
}

export function viewAllowed(view: CashView, perms: CashierPerms): boolean {
  switch (view) {
    case "home":
      return true;
    case "overview":
    case "debts":
      return perms.canDebtEntry;
    case "report":
      return perms.canReports;
    case "confirm":
    case "journal":
      return perms.canPayments;
    case "transactions":
      return perms.canTransactions;
  }
}

/** Порядок пунктов мобильного меню фиксированный — как в спеке. */
const MOBILE_MENU: MobileMenuKey[] = ["confirm", "debts", "transactions", "journal", "report"];
const DESKTOP_VIEWS: CashView[] = ["overview", "confirm", "journal", "transactions"];
const ALL_VIEWS: readonly string[] = ["home", "overview", "report", "debts", "confirm", "journal", "transactions"];

export function mobileMenu(perms: CashierPerms): MobileMenuKey[] {
  return MOBILE_MENU.filter((key) => viewAllowed(key, perms));
}

export function defaultView(perms: CashierPerms, mobile: boolean): CashView {
  if (mobile) {
    // Один доступный пункт — открываем его сразу, главная с одной строкой не нужна.
    const menu = mobileMenu(perms);
    return menu.length === 1 ? menu[0] : "home";
  }
  return DESKTOP_VIEWS.find((view) => viewAllowed(view, perms)) ?? "transactions";
}

/** Экран из `?view=`: чужие для раскладки значения сводятся по таблице спеки, недоступные — к экрану по умолчанию. */
export function resolveView(raw: string | null, perms: CashierPerms, mobile: boolean): CashView {
  const fallback = defaultView(perms, mobile);
  if (!raw || !ALL_VIEWS.includes(raw)) return fallback;
  let view = raw as CashView;
  if (mobile && view === "overview") view = "home";
  if (!mobile && (view === "home" || view === "report" || view === "debts")) view = "overview";
  if (view === "home") return fallback;
  return viewAllowed(view, perms) ? view : fallback;
}
```

- [ ] **Step 4: `filters.ts`** — создать `frontend/src/components/cashier/filters.ts` (типы и `apiUrl`/`filtersAreValid` перенесены из `page.tsx:59-94` без изменений):

```ts
import { toLocalIsoDate } from "@/lib/utils";
import type { CashView } from "./view";

export interface CashFilters {
  dateFrom: string;
  dateTo: string;
  department: string;
  store: string;
  remainingMin: string;
  remainingMax: string;
  remainingCurrency: string;
}

export const EMPTY_CASH_FILTERS: CashFilters = {
  dateFrom: "",
  dateTo: "",
  department: "all",
  store: "all",
  remainingMin: "",
  remainingMax: "",
  remainingCurrency: "all",
};

/** Свои фильтры у каждого экрана: период журнала не должен обрезать «Общее», остаток долга нужен только долгам. */
export type FilterScreen = "overview" | "report" | "debts" | "confirm" | "journal";
export type CashFiltersByScreen = Record<FilterScreen, CashFilters>;

export type PeriodPreset = "today" | "week" | "month" | "all";
export const PERIOD_PRESETS: { key: PeriodPreset; label: string }[] = [
  { key: "today", label: "Сегодня" },
  { key: "week", label: "Неделя" },
  { key: "month", label: "Месяц" },
  { key: "all", label: "Всё" },
];

/** Границы пресета: неделя — последние 7 дней включая сегодня, месяц — с 1-го числа по сегодня. */
export function periodRange(preset: PeriodPreset, now = new Date()): Pick<CashFilters, "dateFrom" | "dateTo"> {
  const today = toLocalIsoDate(now);
  switch (preset) {
    case "today":
      return { dateFrom: today, dateTo: today };
    case "week":
      return { dateFrom: toLocalIsoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6)), dateTo: today };
    case "month":
      return { dateFrom: toLocalIsoDate(new Date(now.getFullYear(), now.getMonth(), 1)), dateTo: today };
    case "all":
      return { dateFrom: "", dateTo: "" };
  }
}

export function periodPresetOf(
  filters: Pick<CashFilters, "dateFrom" | "dateTo">,
  now = new Date(),
): PeriodPreset | "custom" {
  const preset = PERIOD_PRESETS.find(({ key }) => {
    const range = periodRange(key, now);
    return range.dateFrom === filters.dateFrom && range.dateTo === filters.dateTo;
  });
  return preset?.key ?? "custom";
}

/** Отчёт на телефоне открывается за сегодня, как у Kaspi; остальные экраны без ограничений. */
export function initialFilters(now = new Date()): CashFiltersByScreen {
  return {
    overview: EMPTY_CASH_FILTERS,
    report: { ...EMPTY_CASH_FILTERS, ...periodRange("today", now) },
    debts: EMPTY_CASH_FILTERS,
    confirm: EMPTY_CASH_FILTERS,
    journal: EMPTY_CASH_FILTERS,
  };
}

/** Экран фильтров для view; null — у экрана нет панели фильтров (у транзакций свой поиск). */
export function filterScreenFor(view: CashView, mobile: boolean): FilterScreen | null {
  switch (view) {
    case "overview":
      return mobile ? null : "overview";
    case "report":
    case "debts":
      return mobile ? view : null;
    case "confirm":
    case "journal":
      return view;
    default:
      return null;
  }
}

export function apiUrl(path: string, params: Record<string, string>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value && value !== "all") query.set(key, value);
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function filtersAreValid(filters: CashFilters) {
  const datesOk = !filters.dateFrom || !filters.dateTo || filters.dateFrom <= filters.dateTo;
  const min = filters.remainingMin === "" ? null : Number(filters.remainingMin);
  const max = filters.remainingMax === "" ? null : Number(filters.remainingMax);
  const remainingOk = min === null || max === null || min <= max;
  return datesOk && remainingOk;
}

/** Сколько групп фильтров задано — для подписи «Применено: N» и бейджа на иконке. */
export function activeFilterCount(
  filters: CashFilters,
  { dates = true, remaining = false }: { dates?: boolean; remaining?: boolean } = {},
): number {
  return [
    dates && (filters.dateFrom !== "" || filters.dateTo !== ""),
    filters.department !== "all",
    filters.store !== "all",
    remaining && (filters.remainingMin !== "" || filters.remainingMax !== ""),
  ].filter(Boolean).length;
}

/** Общие параметры очереди/журнала/долгов: даты, отдел, магазин. */
export function scopeParams(filters: CashFilters) {
  return {
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    department: filters.department,
    store: filters.store,
  };
}
```

- [ ] **Step 5: `totals.ts`** — создать `frontend/src/components/cashier/totals.ts` (логика перенесена из `page.tsx:903-959`; отличие одно — «Оплат в очереди» суммирует `count` сгруппированных строк, а не считает группы):

```ts
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import type { ClientDebt, PaymentQueueItem, ReportSummary } from "@/lib/types";
import { sumDebtByCurrency, sumMoneyByCurrency } from "@/lib/utils";

export type IncomeSummary = Pick<ReportSummary, "income" | "departments" | "from" | "to">;
/** Строка `/orders/payments-queue/?summary=1`: сумма и число оплат на валюту+способ. */
export type QueueTotal = Pick<PaymentQueueItem, "amount" | "currency" | "method"> & { count?: number };

export interface IncomeTotals {
  currency: string;
  total: number;
  cash: number;
  cashless: number;
  gross: number;
  refunded: number;
  payments: number;
  otherCurrencies: [string, number][];
  otherRefunds: [string, number][];
  grossFor: (currency: string) => number;
}

/** Итоги поступлений в основной валюте; прочие валюты — отдельными парами, без сложения. */
export function incomeTotals(summary: IncomeSummary | null): IncomeTotals {
  const income = summary?.income;
  const byCurrency = income?.by_currency ?? {};
  const currency = income?.currency || Object.keys(byCurrency)[0] || "KZT";
  const pick = (map: Record<string, string> | undefined, legacy: string | undefined) =>
    amountForCurrency(map ?? {}, legacy ?? "0", currency);
  return {
    currency,
    total: pick(byCurrency, income?.total),
    cash: pick(income?.cash_by_currency, income?.cash),
    cashless: pick(income?.cashless_by_currency, income?.cashless),
    gross: pick(income?.gross_by_currency, income?.gross ?? income?.total),
    refunded: pick(income?.refunded_by_currency, income?.refunded),
    payments: income?.payments ?? 0,
    otherCurrencies: otherCurrencyAmounts(byCurrency, currency),
    otherRefunds: otherCurrencyAmounts(income?.refunded_by_currency ?? {}, currency),
    grossFor: (unit) => amountForCurrency(income?.gross_by_currency ?? {}, "0", unit),
  };
}

export interface QueueTotals {
  currency: string;
  total: number;
  cash: number;
  count: number;
  other: [string, number][];
}

export function queueTotals(rows: readonly QueueTotal[]): QueueTotals {
  const byCurrency = sumMoneyByCurrency(
    rows,
    (row) => row.amount,
    (row) => row.currency,
  );
  const cashByCurrency = sumMoneyByCurrency(
    rows.filter((row) => row.method === "cash"),
    (row) => row.amount,
    (row) => row.currency,
  );
  const currency = primaryMoneyCurrency(byCurrency);
  return {
    currency,
    total: byCurrency[currency] ?? 0,
    cash: cashByCurrency[currency] ?? 0,
    count: rows.reduce((sum, row) => sum + (row.count ?? 1), 0),
    other: Object.entries(byCurrency).filter(([unit, value]) => unit !== currency && value > 0),
  };
}

export interface DebtTotals {
  currency: string;
  total: number;
  other: [string, number][];
  clients: number;
  overdue: number;
}

export function debtTotals(rows: readonly ClientDebt[]): DebtTotals {
  const byCurrency = sumDebtByCurrency(rows);
  const currency = primaryMoneyCurrency(byCurrency);
  return {
    currency,
    total: byCurrency[currency] ?? 0,
    other: Object.entries(byCurrency).filter(([unit, value]) => unit !== currency && value > 0),
    clients: rows.length,
    overdue: rows.filter((row) => row.overdue_count > 0).length,
  };
}
```

- [ ] **Step 6: Мелкие общие модули** — создать `frontend/src/components/cashier/debt-state.ts`:

```ts
import type { ClientDebt } from "@/lib/types";

export function debtPaymentState(row: ClientDebt) {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: "warning" as const };
  }
  if (row.partial_count > 0) {
    return { label: "Частично оплачен", tone: "warning" as const };
  }
  return { label: "Не оплачен", tone: "destructive" as const };
}

/** Локальный поиск по списку должников: имя или телефон. */
export function matchesDebtQuery(row: ClientDebt, query: string): boolean {
  return !query || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(query.toLowerCase());
}
```

создать `frontend/src/components/cashier/department-badge.tsx` (перенос из `page.tsx:96-103`):

```tsx
export function DepartmentBadge({ name, color }: { name?: string; color?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold">
      <span className="size-2 rounded-full" style={{ backgroundColor: color ?? "#64748B" }} />
      {name || "Нет отдела"}
    </span>
  );
}
```

создать `frontend/src/components/cashier/action-error.tsx` (перенос из `page.tsx:207-212`):

```tsx
export function ActionError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">{message}</p>
  );
}
```

- [ ] **Step 7: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier && npx tsc --noEmit`
Expected: PASS.

---

### Task 6: Разбор `page.tsx` — общий хук данных, десктопное дерево, экран в URL

Десктоп после задания выглядит и ведёт себя как раньше (плюс `?view=` в адресе). Проверка — существующие тесты `page.test.tsx`.

**Files:**
- Create: `frontend/src/components/cashier/use-cashier-queue.ts`, `use-overdue-check.ts`, `order-review-dialogs.tsx`, `restore-payment-dialog.tsx`, `cash-filter-fields.tsx`, `cash-filters-panel.tsx`, `use-cashier.ts`, `desktop.tsx`
- Modify: `frontend/src/app/accounting/page.tsx` (целиком), `frontend/src/app/accounting/page.test.tsx:19,41-45`

**Interfaces:**
- Consumes: Task 2 (`test-utils/next-navigation`), Task 5 (`view.ts`, `filters.ts`, `totals.ts`, `debt-state.ts`, `department-badge.tsx`, `action-error.tsx`).
- Produces:
  - `useCashierQueue(enabled, canReviewOrders, queueFilters, onChanged?)`, типы `CashierQueue`, `PagedCashierLog` (как в page.tsx сейчас).
  - `useOverdueCheck(reload: () => void): { busy: boolean; message: string; run: () => Promise<void> }`.
  - `OrderReviewDialogs({ q, confirming, rejecting, onConfirmClose, onRejectClose })`, `RestorePaymentDialog({ q, event, onClose })`.
  - `CashFilterFields({ filters, stores, departments, showRemaining, showDates?, layout?: "row" | "stack", onChange })`, `CashFiltersPanel({ filters, stores, departments, showRemaining, onChange, onReset })`.
  - `useCashier({ view, mobile, perms }): CashierModel` — поля перечислены в шаге 7.
  - `CashierDesktop({ model, onTab })`.

- [ ] **Step 1: Перенести очередь кассира** — создать `frontend/src/components/cashier/use-cashier-queue.ts`: скопировать из `frontend/src/app/accounting/page.tsx` строки 115–205 (`useCashierQueue`, `type CashierQueue`, `type PagedCashierLog`) без изменений тела, с таким заголовком и импортами:

```ts
"use client";
import { useCallback, useRef, useState } from "react";
import type { OrderConfirmationData } from "@/components/order-confirmation";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { CashierLogItem, Order, PaymentQueueItem } from "@/lib/types";
import { usePagedApi } from "@/lib/use-paged-api";
import { apiUrl, filtersAreValid, scopeParams, type CashFilters } from "./filters";
```

Единственная правка внутри: блок `const queueParams = { date_from: ..., store: queueFilters.store };` заменить на `const queueParams = scopeParams(queueFilters);` (тот же порядок ключей). Экспортировать `export function useCashierQueue`, `export type CashierQueue`, `export type PagedCashierLog`.

- [ ] **Step 2: Общие диалоги и проверка просрочек** — создать `frontend/src/components/cashier/use-overdue-check.ts`:

```ts
"use client";
import { useState } from "react";
import { api, apiError } from "@/lib/api";

/** «Проверить просрочки» — общий для десктопной таблицы и мобильного списка долгов. */
export function useOverdueCheck(reload: () => void) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function run() {
    setBusy(true);
    setMessage("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setMessage(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      reload();
    } catch (e) {
      setMessage(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return { busy, message, run };
}
```

создать `frontend/src/components/cashier/order-review-dialogs.tsx`:

```tsx
"use client";
import { OrderConfirmation } from "@/components/order-confirmation";
import { OrderRejectionDialog } from "@/components/order-rejection-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import type { Department, Order } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { ActionError } from "./action-error";
import type { CashierQueue } from "./use-cashier-queue";

/** Подтверждение и отклонение заявки — одни и те же окна на десктопе и телефоне. */
export function OrderReviewDialogs({
  q,
  confirming,
  rejecting,
  onConfirmClose,
  onRejectClose,
}: {
  q: CashierQueue;
  confirming: Order | null;
  rejecting: Order | null;
  onConfirmClose: () => void;
  onRejectClose: () => void;
}) {
  const {
    data: departments,
    error: departmentsError,
    reload: retryDepartments,
  } = useApi<Department[]>(confirming ? "/departments/" : null);
  return (
    <>
      {rejecting && (
        <OrderRejectionDialog
          key={rejecting.id}
          order={rejecting}
          onClose={onRejectClose}
          onDone={() => {
            onRejectClose();
            void q.reload();
          }}
        />
      )}
      <Modal
        open={!!confirming}
        onClose={() => {
          if (!q.busy) onConfirmClose();
        }}
        eyebrow="Подтверждение"
        title={`Заказ #${confirming?.id ?? ""}`}
        mobileFullscreen
      >
        <ActionError message={q.error} />
        {departmentsError ? (
          <ErrorAlert message={departmentsError} onRetry={retryDepartments} />
        ) : (
          confirming && (
            <OrderConfirmation
              key={confirming.id}
              order={confirming}
              departments={departments ?? []}
              busy={q.busy}
              onConfirm={async (payload) => {
                if (await q.confirmOrder(confirming, payload)) onConfirmClose();
              }}
            />
          )
        )}
      </Modal>
    </>
  );
}
```

создать `frontend/src/components/cashier/restore-payment-dialog.tsx`:

```tsx
"use client";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { CashierLogItem } from "@/lib/types";
import type { CashierQueue } from "./use-cashier-queue";

export function RestorePaymentDialog({
  q,
  event,
  onClose,
}: {
  q: CashierQueue;
  event: CashierLogItem | null;
  onClose: () => void;
}) {
  return (
    <ConfirmDialog
      open={!!event}
      onClose={onClose}
      title="Восстановить отклонённую оплату?"
      description={
        event ? `Оплата по заказу #${event.order} вернётся в очередь кассы. Само событие отмены останется в журнале.` : ""
      }
      confirmLabel="Восстановить"
      confirmVariant="default"
      busy={q.busy}
      error={q.error}
      onConfirm={async () => {
        if (!event) return;
        await q.restorePayment(event);
        onClose();
      }}
    />
  );
}
```

- [ ] **Step 3: Поля фильтров** — создать `frontend/src/components/cashier/cash-filter-fields.tsx` (раскладка `row` — точная копия полей из `CashFiltersPanel`; `stack` — для шторки: нативные `Select`, поля во всю ширину):

```tsx
"use client";
import { FilterDropdown, type FilterOption } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { Department, Store } from "@/lib/types";
import { cn } from "@/lib/utils";
import type { CashFilters } from "./filters";

const CURRENCY_OPTIONS: FilterOption[] = [
  { key: "all", label: "Основная" },
  { key: "KZT", label: "KZT" },
  { key: "USD", label: "USD" },
];

/** Поля фильтров кассы. `row` — десктопная панель, `stack` — мобильная шторка (нативные select открывают системный пикер). */
export function CashFilterFields({
  filters,
  stores,
  departments,
  showRemaining,
  showDates = true,
  layout = "row",
  onChange,
}: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  /** Диапазон остатка долга уместен только там, где есть долги. */
  showRemaining: boolean;
  /** Отчёт на телефоне выбирает период чипами — даты в шторке не нужны. */
  showDates?: boolean;
  layout?: "row" | "stack";
  onChange: (patch: Partial<CashFilters>) => void;
}) {
  const stack = layout === "stack";
  const departmentOptions: FilterOption[] = [
    { key: "all", label: "Все" },
    ...departments.map((department) => ({ key: department.code, label: department.name })),
  ];
  const storeOptions: FilterOption[] = [
    { key: "all", label: "Все" },
    ...[...stores]
      .sort((a, b) => a.name.localeCompare(b.name, "ru"))
      .map((store) => ({ key: String(store.id), label: store.name })),
  ];
  const datesInvalid = Boolean(filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo);
  const remainingInvalid =
    showRemaining &&
    Boolean(
      filters.remainingMin && filters.remainingMax && Number(filters.remainingMin) > Number(filters.remainingMax),
    );
  const labelClass = "text-[11px] font-medium text-[var(--muted-foreground)]";

  function choice(label: string, active: string, options: FilterOption[], pick: (key: string) => void) {
    if (!stack) return <FilterDropdown label={label} active={active} onChange={pick} options={options} />;
    return (
      <label className="flex flex-col gap-1.5">
        <span className={labelClass}>{label}</span>
        <Select value={active} onChange={(event) => pick(event.target.value)}>
          {options.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </Select>
      </label>
    );
  }

  return (
    <div className={cn("flex gap-3", stack ? "flex-col" : "flex-wrap items-end")}>
      {showDates && (
        <div className={cn(stack ? "grid grid-cols-2 gap-3" : "contents")}>
          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>С даты</span>
            <Input
              type="date"
              value={filters.dateFrom}
              onChange={(e) => onChange({ dateFrom: e.target.value })}
              className={stack ? "h-10" : "h-9 w-[158px]"}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>По дату</span>
            <Input
              type="date"
              value={filters.dateTo}
              onChange={(e) => onChange({ dateTo: e.target.value })}
              className={stack ? "h-10" : "h-9 w-[158px]"}
            />
          </label>
        </div>
      )}
      {choice("Отдел", filters.department, departmentOptions, (department) => onChange({ department }))}
      {choice("Магазин", filters.store, storeOptions, (store) => onChange({ store }))}
      {showRemaining && (
        <div className="flex flex-col gap-1.5">
          <span className={labelClass}>Остаток долга</span>
          <div className={cn("flex items-center gap-1.5", stack && "flex-wrap")}>
            {stack ? (
              <Select
                aria-label="Валюта остатка"
                className="w-auto"
                value={filters.remainingCurrency}
                onChange={(event) => onChange({ remainingCurrency: event.target.value })}
              >
                {CURRENCY_OPTIONS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </Select>
            ) : (
              <FilterDropdown
                label="Валюта"
                active={filters.remainingCurrency}
                onChange={(remainingCurrency) => onChange({ remainingCurrency })}
                options={CURRENCY_OPTIONS}
              />
            )}
            <Input
              type="number"
              min="0"
              inputMode="decimal"
              placeholder="От"
              value={filters.remainingMin}
              onChange={(e) => onChange({ remainingMin: e.target.value })}
              className={stack ? "h-10 flex-1" : "h-9 w-[118px]"}
            />
            <span className="text-[var(--muted-foreground)]">—</span>
            <Input
              type="number"
              min="0"
              inputMode="decimal"
              placeholder="До"
              value={filters.remainingMax}
              onChange={(e) => onChange({ remainingMax: e.target.value })}
              className={stack ? "h-10 flex-1" : "h-9 w-[118px]"}
            />
          </div>
        </div>
      )}
      {(datesInvalid || remainingInvalid) && (
        <p className="w-full text-xs font-medium text-[var(--destructive)]">
          {datesInvalid
            ? "Дата начала не может быть позже даты окончания."
            : "Минимальный остаток не может быть больше максимального."}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Десктопная панель** — создать `frontend/src/components/cashier/cash-filters-panel.tsx` (шапка панели из `page.tsx:671-692`, поля — `CashFilterFields`):

```tsx
"use client";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Department, Store } from "@/lib/types";
import { CashFilterFields } from "./cash-filter-fields";
import { activeFilterCount, type CashFilters } from "./filters";

export function CashFiltersPanel({
  filters,
  stores,
  departments,
  showRemaining,
  onChange,
  onReset,
}: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  showRemaining: boolean;
  onChange: (patch: Partial<CashFilters>) => void;
  onReset: () => void;
}) {
  const activeCount = activeFilterCount(filters, { remaining: showRemaining });
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--muted-foreground)]">
                <SlidersHorizontal className="size-4" />
              </span>
              <div>
                <div className="text-sm font-semibold">Фильтры кассы</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  {activeCount ? `Применено: ${activeCount}` : "Без ограничений · все оплаты"}
                </div>
              </div>
            </div>
            {activeCount > 0 && (
              <Button size="sm" variant="ghost" onClick={onReset}>
                <X className="size-4" /> Сбросить
              </Button>
            )}
          </div>
          <CashFilterFields
            filters={filters}
            stores={stores}
            departments={departments}
            showRemaining={showRemaining}
            onChange={onChange}
          />
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 5: Общий хук данных** — создать `frontend/src/components/cashier/use-cashier.ts` (матрица загрузки из спеки: какой экран что грузит):

```ts
"use client";
import { useCallback, useState } from "react";
import type { CashierLogItem, ClientDebt, Department, Order, Store } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import {
  EMPTY_CASH_FILTERS,
  apiUrl,
  filterScreenFor,
  filtersAreValid,
  initialFilters,
  periodRange,
  scopeParams,
  type CashFilters,
  type CashFiltersByScreen,
} from "./filters";
import { debtTotals, incomeTotals, queueTotals, type IncomeSummary, type QueueTotal } from "./totals";
import { useCashierQueue } from "./use-cashier-queue";
import type { CashView, CashierPerms } from "./view";

/**
 * Данные кассы для обеих раскладок. Активные запросы зависят от экрана:
 * «Общее» (десктоп) — сводка/долги/очередь по своим фильтрам; главная
 * (телефон) — те же три запроса без фильтров, сводка строго за сегодня;
 * отчёт и долги на телефоне — свои фильтры; очередь и журнал — одинаково везде.
 */
export function useCashier({ view, mobile, perms }: { view: CashView; mobile: boolean; perms: CashierPerms }) {
  const [filtersByScreen, setFiltersByScreen] = useState<CashFiltersByScreen>(initialFilters);
  const filterScreen = filterScreenFor(view, mobile);
  const filters = filterScreen ? filtersByScreen[filterScreen] : EMPTY_CASH_FILTERS;

  const patchFilters = useCallback(
    (patch: Partial<CashFilters>) => {
      if (!filterScreen) return;
      setFiltersByScreen((current) => ({ ...current, [filterScreen]: { ...current[filterScreen], ...patch } }));
    },
    [filterScreen],
  );
  const resetFilters = useCallback(() => {
    if (!filterScreen) return;
    setFiltersByScreen((current) => ({ ...current, [filterScreen]: EMPTY_CASH_FILTERS }));
  }, [filterScreen]);

  const overviewActive = !mobile && view === "overview";
  const homeActive = mobile && view === "home";
  const reportActive = mobile && view === "report";
  const debtsActive = mobile && view === "debts";

  const summaryFilters = overviewActive
    ? filtersByScreen.overview
    : reportActive
      ? filtersByScreen.report
      : homeActive
        ? { ...EMPTY_CASH_FILTERS, ...periodRange("today") }
        : null;
  const debtsFilters = overviewActive
    ? filtersByScreen.overview
    : debtsActive
      ? filtersByScreen.debts
      : homeActive
        ? EMPTY_CASH_FILTERS
        : null;
  const queueSummaryFilters = overviewActive ? filtersByScreen.overview : homeActive ? EMPTY_CASH_FILTERS : null;

  const summaryUrl =
    perms.canReports && summaryFilters && filtersAreValid(summaryFilters)
      ? apiUrl("/reports/summary/", {
          section: "income",
          from: summaryFilters.dateFrom,
          to: summaryFilters.dateTo,
          department: summaryFilters.department,
          store: summaryFilters.store,
        })
      : null;
  const debtsUrl =
    perms.canDebtEntry && debtsFilters && filtersAreValid(debtsFilters)
      ? apiUrl("/clients/debts/", {
          ...scopeParams(debtsFilters),
          remaining_min: debtsFilters.remainingMin,
          remaining_max: debtsFilters.remainingMax,
          remaining_currency: debtsFilters.remainingCurrency,
        })
      : null;
  const queueSummaryUrl =
    perms.canPayments && queueSummaryFilters && filtersAreValid(queueSummaryFilters)
      ? apiUrl("/orders/payments-queue/", { summary: "1", ...scopeParams(queueSummaryFilters) })
      : null;

  // Кассовая аналитика — тот же серверный отчёт, что и на «Отчётах».
  const summary = useApi<IncomeSummary>(summaryUrl);
  const debts = useApi<ClientDebt[]>(debtsUrl);
  const queueSummary = useApi<QueueTotal[]>(queueSummaryUrl);
  // Главной нужно только число заявок; сами заявки грузит экран очереди.
  const pendingCount = usePagedApi<Order>(homeActive && perms.canReviewOrders ? "/orders/?status_group=pending" : null, 1);
  const journalFilters = filtersByScreen.journal;
  const journalLog = usePagedApi<CashierLogItem>(
    perms.canPayments && view === "journal" && filtersAreValid(journalFilters)
      ? apiUrl("/orders/cashier-log/", scopeParams(journalFilters))
      : null,
    50,
  );
  const { data: stores } = useApi<Store[]>(perms.canReports && perms.canViewClients ? "/stores/" : null);
  const { data: departments } = useApi<Department[]>("/departments/");

  const { reload: reloadSummary } = summary;
  const { reload: reloadDebts } = debts;
  const { reload: reloadQueueSummary } = queueSummary;
  const { reload: reloadPendingCount } = pendingCount;
  const { reload: reloadJournal } = journalLog;
  const reloadOverview = useCallback(async () => {
    await Promise.all([reloadSummary(), reloadDebts(), reloadQueueSummary(), reloadPendingCount()]);
  }, [reloadDebts, reloadPendingCount, reloadQueueSummary, reloadSummary]);
  const paymentChanged = useCallback(async () => {
    await Promise.all([reloadOverview(), reloadJournal()]);
  }, [reloadJournal, reloadOverview]);

  const queue = useCashierQueue(perms.canPayments && view === "confirm", perms.canReviewOrders, filtersByScreen.confirm, paymentChanged);

  const overviewValid = !overviewActive || filtersAreValid(filtersByScreen.overview);
  useVisiblePolling(reloadOverview, 30_000, (overviewActive || homeActive) && overviewValid && !queue.busy);
  // Preserve rows the cashier explicitly expanded; manual refresh and
  // completed actions still reload the queue from its first page.
  useVisiblePolling(
    queue.refresh,
    30_000,
    perms.canPayments &&
      view === "confirm" &&
      !queue.busy &&
      !queue.pendingPage.loadingMore &&
      !queue.queuePage.loadingMore &&
      queue.pendingOrders.length <= 50 &&
      queue.toReview.length <= 50,
  );

  const debtRows = debts.data ?? [];
  return {
    perms,
    view,
    mobile,
    filters,
    filterScreen,
    filtersByScreen,
    patchFilters,
    resetFilters,
    summary,
    debts,
    queueSummary,
    pendingCount,
    journalLog,
    queue,
    stores: stores ?? [],
    departments: departments ?? [],
    income: incomeTotals(summary.data),
    incomeReady: !summary.loading && !summary.error && summary.data !== null,
    queueTotals: queueTotals(queueSummary.data ?? []),
    queueReady: !queueSummary.loading && !queueSummary.error && queueSummary.data !== null,
    debtRows,
    debtTotals: debtTotals(debtRows),
    debtsReady: !debts.loading && !debts.error && debts.data !== null,
    reloadOverview,
    paymentChanged,
  };
}

export type CashierModel = ReturnType<typeof useCashier>;
```

- [ ] **Step 6: Десктопное дерево** — создать `frontend/src/components/cashier/desktop.tsx`. Перенести из `frontend/src/app/accounting/page.tsx` без изменений тела списков/таблиц три секции: `ConfirmQueueSection` (строки 214–392), `PaymentJournalSection` (394–466), `DebtsSection` (468–640) — и применить к ним три правки ниже. Затем добавить `CashierDesktop` (разметка бывшего `return` из `CashierInner`, строки 984–1134, переписанная на `model`).

Импорты файла:

```tsx
"use client";
import Link from "next/link";
import { useState } from "react";
import dynamic from "next/dynamic";
import { ArrowUpRight, RefreshCw, Search } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PaymentStageBadge } from "@/components/payment-chain";
import { DepartmentComparison } from "@/components/reports/department-comparison";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { SummaryCard } from "@/components/ui/summary-card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import type { CashierLogItem, ClientDebt, Order } from "@/lib/types";
import { cn, formatCurrency, formatDateTime, todayLocalIsoDate } from "@/lib/utils";
import { ActionError } from "./action-error";
import { CashFiltersPanel } from "./cash-filters-panel";
import { debtPaymentState, matchesDebtQuery } from "./debt-state";
import { DepartmentBadge } from "./department-badge";
import { OrderReviewDialogs } from "./order-review-dialogs";
import { RestorePaymentDialog } from "./restore-payment-dialog";
import type { CashierModel } from "./use-cashier";
import type { CashierQueue, PagedCashierLog } from "./use-cashier-queue";
import { useOverdueCheck } from "./use-overdue-check";
import type { CashView } from "./view";

const TransactionsSection = dynamic(() =>
  import("@/components/transactions-section").then((m) => m.TransactionsSection),
);
```

Правка 1 — начало `ConfirmQueueSection`: вместо локального `useApi` отделов и разметки `{rejecting && (...)}` + `<Modal ...>...</Modal>` (строки 229–271) оставить:

```tsx
  const [confirming, setConfirming] = useState<Order | null>(null);
  const [rejecting, setRejecting] = useState<Order | null>(null);
  return (
    <section className="flex flex-col gap-4">
      <OrderReviewDialogs
        q={q}
        confirming={confirming}
        rejecting={rejecting}
        onConfirmClose={() => setConfirming(null)}
        onRejectClose={() => setRejecting(null)}
      />
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}
```

(далее — `<div className={cn("grid grid-cols-1 gap-6", ...)}>` и всё до конца секции без изменений).

Правка 2 — конец `PaymentJournalSection`: блок `<ConfirmDialog open={!!restoreEvent} ... />` (строки 445–463) заменить на:

```tsx
      <RestorePaymentDialog q={q} event={restoreEvent} onClose={() => setRestoreEvent(null)} />
```

Правка 3 — начало `DebtsSection`: строки 482–506 (`useState` для `checkMsg`/`busy`, `filtered`, `checkOverdue`) заменить на:

```tsx
  const [q, setQ] = useState("");
  const overdue = useOverdueCheck(reload);
  // Данные уже загружены целиком — лениво рендерим, чтобы длинный список
  // должников не разворачивался простынёй.
  const [limit, setLimit] = useState(25);

  const filtered = rows.filter((row) => matchesDebtQuery(row, q));
  const visible = filtered.slice(0, limit);
```

и ниже в разметке: `disabled={busy} onClick={checkOverdue}` → `disabled={overdue.busy} onClick={() => void overdue.run()}`, `(busy ? " animate-spin" : "")` → `(overdue.busy ? " animate-spin" : "")`, `{checkMsg && (` → `{overdue.message && (`, `{checkMsg}` → `{overdue.message}`.

`CashierDesktop` (в конец файла):

```tsx
export function CashierDesktop({ model, onTab }: { model: CashierModel; onTab: (view: CashView) => void }) {
  const {
    view,
    perms,
    filters,
    filterScreen,
    filtersByScreen,
    patchFilters,
    resetFilters,
    summary,
    queueSummary,
    debts,
    income,
    incomeReady,
    queueTotals,
    queueReady,
    debtRows,
    debtTotals,
    debtsReady,
    journalLog,
    queue,
    stores,
    departments,
  } = model;
  const money = formatCurrency;
  const overviewFilters = filtersByScreen.overview;
  const today = todayLocalIsoDate();
  const isToday = overviewFilters.dateFrom === today && overviewFilters.dateTo === today;
  const hasDates = Boolean(overviewFilters.dateFrom || overviewFilters.dateTo);

  const tabs: TabDef[] = [
    ...(perms.canDebtEntry ? [{ key: "overview", label: perms.canReports ? "Общее" : "Долги" }] : []),
    ...(perms.canPayments
      ? [
          {
            key: "confirm",
            label: "Заявки и оплаты",
            count: view === "confirm" && !queue.loading ? queue.pendingPage.count + queue.queuePage.count : undefined,
          },
          { key: "journal", label: "Журнал" },
        ]
      : []),
    ...(perms.canTransactions ? [{ key: "transactions", label: "Транзакции" }] : []),
  ];

  return (
    <AppShell
      title="Касса"
      section="Работа"
      description="Поступления, очередь подтверждений, долги и транзакции в одном месте."
    >
      <div className="flex flex-col gap-6">
        <Tabs
          className="overflow-x-auto whitespace-nowrap"
          tabs={tabs}
          active={view}
          onChange={(key) => onTab(key as CashView)}
        />

        {/* У транзакций свой поиск — фильтры кассы к ним не применяются.
            Панель правит фильтры только текущей вкладки. */}
        {filterScreen && (
          <CashFiltersPanel
            filters={filters}
            stores={stores}
            departments={departments}
            showRemaining={filterScreen === "overview"}
            onChange={patchFilters}
            onReset={resetFilters}
          />
        )}

        {view === "overview" && perms.canDebtEntry && (
          <>
            {perms.canReports && summary.error && <ErrorAlert message={summary.error} onRetry={summary.reload} />}
            {perms.canPayments && queueSummary.error && (
              <ErrorAlert message={queueSummary.error} onRetry={queueSummary.reload} />
            )}
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {perms.canReports && (
                <SummaryCard
                  title={
                    isToday
                      ? "Чистое поступление сегодня"
                      : hasDates
                        ? "Чистое поступление за период"
                        : "Чистое поступление за всё время"
                  }
                  tone={income.total < 0 ? "destructive" : "success"}
                  value={incomeReady ? money(income.total, income.currency) : "—"}
                  rows={
                    !incomeReady
                      ? []
                      : [
                          { label: "Наличные, нетто", value: money(income.cash, income.currency) },
                          { label: "Безналичные, нетто", value: money(income.cashless, income.currency) },
                          ...income.otherCurrencies.map(([currency, value]) => ({
                            label: "Также чистыми",
                            value: money(value, currency),
                          })),
                          ...(income.refunded > 0
                            ? [
                                { label: "Поступило до возвратов", value: money(income.gross, income.currency) },
                                { label: "Возвращено", value: money(income.refunded, income.currency) },
                              ]
                            : []),
                          ...income.otherRefunds.flatMap(([currency, value]) => [
                            {
                              label: `Поступило до возвратов, ${currency}`,
                              value: money(income.grossFor(currency), currency),
                            },
                            { label: `Возвращено, ${currency}`, value: money(value, currency) },
                          ]),
                        ]
                  }
                />
              )}
              {perms.canPayments && (
                <SummaryCard
                  title="Ожидает подтверждения"
                  tone="primary"
                  value={queueReady ? money(queueTotals.total, queueTotals.currency) : "—"}
                  rows={
                    !queueReady
                      ? []
                      : [
                          ...queueTotals.other.map(([currency, value]) => ({
                            label: "Также в очереди",
                            value: money(value, currency),
                          })),
                          { label: "Оплат в очереди", value: String(queueTotals.count) },
                          { label: "Из них наличными", value: money(queueTotals.cash, queueTotals.currency) },
                        ]
                  }
                />
              )}
              <SummaryCard
                title="Дебиторка"
                tone="destructive"
                value={debtsReady ? money(debtTotals.total, debtTotals.currency) : "—"}
                rows={
                  !debtsReady
                    ? []
                    : [
                        ...debtTotals.other.map(([currency, value]) => ({
                          label: "Также в долге",
                          value: money(value, currency),
                        })),
                        { label: "Клиентов с долгом", value: String(debtTotals.clients) },
                        { label: "С просрочкой", value: String(debtTotals.overdue) },
                      ]
                }
              />
            </section>

            {perms.canReports && summary.data?.departments && (
              <DepartmentComparison
                rows={summary.data.departments}
                incomeOnly
                from={summary.data.from}
                to={summary.data.to}
              />
            )}
            <DebtsSection
              rows={debtRows}
              loading={debts.loading}
              error={debts.error}
              reload={debts.reload}
              canCheckOverdue={perms.canCheckOverdue}
            />
          </>
        )}

        {view === "confirm" && perms.canPayments && (
          <ConfirmQueueSection
            q={queue}
            canViewOrders={perms.canViewOrders}
            canReviewOrders={perms.canReviewOrders}
            canReceivePayments={perms.canPayments}
          />
        )}

        {view === "journal" && perms.canPayments && <PaymentJournalSection q={queue} log={journalLog} />}

        {view === "transactions" && perms.canTransactions && (
          <TransactionsSection
            canConfirm={perms.canPayments}
            canCreate={perms.canCreatePayments}
            departments={departments}
            onChanged={queue.reload}
          />
        )}
      </div>
    </AppShell>
  );
}
```

- [ ] **Step 7: Тонкая страница** — заменить содержимое `frontend/src/app/accounting/page.tsx` целиком на:

```tsx
"use client";
import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CashierDesktop } from "@/components/cashier/desktop";
import { useCashier } from "@/components/cashier/use-cashier";
import { CASHIER_ENTRY_PERMS, cashierPerms, resolveView, type CashView } from "@/components/cashier/view";
import { RequirePerm } from "@/components/require-perm";
import { useAuth } from "@/store/auth";

function CashierInner() {
  const { me } = useAuth();
  const perms = cashierPerms(me);
  // Мобильная раскладка подключается следующим шагом плана.
  const mobile = false;
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Экран живёт в URL: диплинки открывают нужную вкладку, «назад» работает.
  const view = resolveView(searchParams.get("view"), perms, mobile);
  const model = useCashier({ view, mobile, perms });

  function selectTab(next: CashView) {
    router.replace(`${pathname}?view=${next}`, { scroll: false });
  }

  return <CashierDesktop model={model} onTab={selectTab} />;
}

export default function CashierPage() {
  // Доступ, если есть хотя бы одна из секций: очередь, аналитика с долгами или транзакции.
  return (
    <RequirePerm perm={CASHIER_ENTRY_PERMS} title="Касса">
      <Suspense fallback={null}>
        <CashierInner />
      </Suspense>
    </RequirePerm>
  );
}
```

- [ ] **Step 8: Мок навигации в тестах страницы** — в `frontend/src/app/accounting/page.test.tsx`:
  - строку `vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));` заменить на `vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));`
  - добавить импорт `import { resetNavigation } from "@/test-utils/next-navigation";`
  - в начало `beforeEach` добавить `resetNavigation("/accounting");`.

- [ ] **Step 9: Старые тесты страницы зелёные, типы сходятся**

Run: `cd frontend && npx vitest run src/app/accounting/page.test.tsx src/components/cashier && npx tsc --noEmit && npx eslint src/app/accounting src/components/cashier --max-warnings=0`
Expected: 4 сценария `page.test.tsx` PASS (вкладки переключаются через мок-роутер), tsc и eslint чистые. В `page.tsx` не должно остаться ни одной секции — файл ~35 строк.

---

### Task 7: Разбор «Транзакций» — хук, действия, детали, модалки

Десктопная таблица остаётся прежней; появляются общие блоки для мобильного списка. Проверка — `transactions-section.test.tsx` без правок + новый тест действий.

**Files:**
- Create: `frontend/src/components/transactions/use-transactions.ts`, `transaction-actions.tsx`, `transaction-actions.test.tsx`, `transaction-detail.tsx`, `transaction-modals.tsx`, `paid-method-summary.tsx`
- Modify: `frontend/src/components/transactions-section.tsx` (целиком)

**Interfaces:**
- Consumes: `Chip` (Task 4).
- Produces:
  - `useTransactions({ onChanged? })` → `Transactions`: `query, setQuery, statusFilter, setStatusFilter, department, setDepartment, data, loading, loadError, reload, rows, meta, statusItems, refreshFromStart, page, busy, error, receipt, issue, refund, reject, restore, refundFor, setRefundFor, statusFor, rejectFor, setRejectFor, restoreFor, setRestoreFor, qrFor, setQrFor, rejectReason, setRejectReason, amount, setAmount, reason, setReason, openRefund, openReject, openRestore, openStatus, closeStatus`; экспорт `STATUS_FILTERS`, `TransactionPage`.
  - `transactionActions(payment, handlers, { canConfirm, canCreate }): TransactionAction[]`, `TransactionActionHandlers`, `TransactionActions({ actions, layout: "icons" | "list" })`.
  - `TransactionDetail({ payment })`, `TransactionModals({ t, canConfirm, canCreate, sheet?, detailActions? })`, `PaidMethodSummary({ summary? })`.

- [ ] **Step 1: Падающий тест действий** — создать `frontend/src/components/transactions/transaction-actions.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { TransactionActions, transactionActions, type TransactionActionHandlers } from "./transaction-actions";

const confirmed: Payment = {
  id: 5,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  status: "confirmed",
  paid_at: "2026-09-12T10:00:00",
  recorded_by: null,
  available_for_refund: "100",
};

function handlers(): TransactionActionHandlers {
  return { busy: false, receipt: vi.fn(), issue: vi.fn(), openRefund: vi.fn(), openReject: vi.fn(), openRestore: vi.fn() };
}

it("offers receipt and refund for a confirmed payment when the cashier may confirm", async () => {
  const h = handlers();
  const actions = transactionActions(confirmed, h, { canConfirm: true, canCreate: false });
  expect(actions.map((action) => action.key)).toEqual(["receipt", "refund"]);
  render(<TransactionActions actions={actions} layout="list" />);
  await userEvent.click(screen.getByRole("button", { name: /Вернуть оплату/ }));
  expect(h.openRefund).toHaveBeenCalledWith(confirmed);
  expect(screen.getByRole("button", { name: /Скачать выписку/ })).toBeInTheDocument();
});

it("hides money actions without the confirm permission and keeps icon titles", () => {
  const actions = transactionActions(confirmed, handlers(), { canConfirm: false, canCreate: false });
  expect(actions.map((action) => action.key)).toEqual(["receipt"]);
  render(<TransactionActions actions={actions} layout="icons" />);
  expect(screen.getByRole("button", { name: "Скачать выписку ASYL LTD" })).toBeInTheDocument();
});

it("lets the cashier reject a manual payment that is still open", () => {
  const actions = transactionActions({ ...confirmed, status: "received" }, handlers(), { canConfirm: true, canCreate: true });
  expect(actions.map((action) => action.key)).toEqual(["reject"]);
});
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd frontend && npx vitest run src/components/transactions`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Хук** — создать `frontend/src/components/transactions/use-transactions.ts`. Тело — перенос из `transactions-section.tsx:208-364` (состояние, накопление страниц, счётчики, мутации) с добавлением открывателей модалок:

```ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import { PAYMENT_STAGE_LABELS } from "@/lib/constants";
import { downloadBlob } from "@/lib/download";
import type { Payment } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { useVisiblePolling } from "@/lib/use-visible-polling";

export interface TransactionPage {
  results: Payment[];
  count: number;
  page: number;
  pages: number;
  /** Счётчики по статусам при текущем поиске (до статус-фильтра). */
  status_counts?: Record<string, number>;
  summary: {
    paid_by_currency: { KZT: string; USD: string };
    refunded_by_currency: { KZT: string; USD: string };
    /** {валюта: {способ: чистая сумма}} — сумма по способам равна итогу. */
    paid_by_method: Record<string, Record<string, string>>;
  };
}

/** Пилюли статус-фильтра: подписи из общего словаря этапов оплат. */
export const STATUS_FILTERS = [
  { key: "requested", label: PAYMENT_STAGE_LABELS.requested ?? "Ожидает" },
  { key: "received", label: PAYMENT_STAGE_LABELS.received ?? "Принята" },
  { key: "confirmed", label: PAYMENT_STAGE_LABELS.confirmed ?? "Подтверждена" },
  { key: "rejected", label: PAYMENT_STAGE_LABELS.rejected ?? "Отклонена" },
];

/** Данные и действия ленты транзакций — общие для десктопной таблицы и мобильного списка. */
export function useTransactions({ onChanged }: { onChanged?: () => Promise<unknown> } = {}) {
  const [page, setPage] = useState(1);
  const [query, setQueryState] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [department, setDepartmentState] = useState("all");
  const debouncedQuery = useDebounced(query.trim());
  useEffect(() => setPage(1), [debouncedQuery, department, statusFilter]);
  const transactionParams = new URLSearchParams({
    page: String(page),
    page_size: "50",
    search: debouncedQuery,
  });
  if (statusFilter !== "all") transactionParams.set("status", statusFilter);
  if (department !== "all") transactionParams.set("department", department);
  const {
    data,
    loading,
    error: loadError,
    reload,
  } = useApi<TransactionPage>(`/payment-transactions/?${transactionParams.toString()}`);
  // Единый стиль пагинации: страницы накапливаются под «Показать ещё»,
  // а не листаются взад-вперёд. Итоги в конверте всегда по всей выборке.
  const [rows, setRows] = useState<Payment[]>([]);
  // Конверт держим отдельно от data: useApi зануляет data на время запроса,
  // а кнопка «Показать ещё» не должна пропадать, пока грузится страница.
  const [meta, setMeta] = useState<{ page: number; pages: number; count: number } | null>(null);
  useEffect(() => {
    if (!data) return;
    setMeta({ page: data.page, pages: data.pages, count: data.count });
    setRows((current) => {
      if (data.page <= 1) return data.results;
      // Смещение страниц может сдвинуться из-за новых оплат — дубликаты
      // строк (и React-ключей) отфильтровываем по id.
      const seen = new Set(current.map((row) => row.id));
      return [...current, ...data.results.filter((row) => !seen.has(row.id))];
    });
  }, [data]);
  useEffect(() => {
    // Новый поиск или статус — новый список: старые накопленные строки не
    // должны выглядеть результатом свежего запроса.
    setRows([]);
    setMeta(null);
  }, [debouncedQuery, department, statusFilter]);
  // Счётчики статусов приходят до статус-фильтра и живут между запросами,
  // чтобы пилюли не мигали на каждую загрузку.
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  useEffect(() => setStatusCounts({}), [department]);
  useEffect(() => {
    if (data?.status_counts) setStatusCounts(data.status_counts);
  }, [data]);
  const statusItems = [
    { key: "all", label: "Все", count: Object.values(statusCounts).reduce((s, n) => s + n, 0) },
    ...STATUS_FILTERS.map((item) => ({ ...item, count: statusCounts[item.key] ?? 0 })),
  ];

  // После действий (подтвердить/возврат/восстановить) лента начинается с
  // первой страницы — иначе накопленные строки разъедутся с сервером.
  const refreshFromStart = useCallback(() => {
    if (page === 1) return reload();
    setPage(1);
    return Promise.resolve();
  }, [page, reload]);
  function loadNextPage() {
    setPage((value) => value + 1);
  }
  const [refundFor, setRefundFor] = useState<Payment | null>(null);
  const [statusFor, setStatusFor] = useState<Payment | null>(null);
  const [rejectFor, setRejectFor] = useState<Payment | null>(null);
  const [restoreFor, setRestoreFor] = useState<Payment | null>(null);
  const [qrFor, setQrFor] = useState<Payment | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mutationInFlight = useRef(false);
  useVisiblePolling(reload, 15_000, page === 1 && !busy);

  function setQuery(value: string) {
    setQueryState(value);
    setPage(1);
  }
  function setDepartment(value: string) {
    setPage(1);
    setDepartmentState(value);
  }

  async function receipt(payment: Payment) {
    setError("");
    try {
      const response = await api.get<Blob>(`/payment-transactions/${payment.id}/receipt/`, {
        responseType: "blob",
      });
      downloadBlob(response.data, `receipt_${payment.id}.pdf`);
    } catch (e) {
      setError(apiError(e));
    }
  }

  async function refund() {
    if (!refundFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${refundFor.id}/refund/`, {
        amount: amount || undefined,
        reason,
        mode: "auto",
      });
      setRefundFor(null);
      setAmount("");
      setReason("");
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function reject() {
    if (!rejectFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${rejectFor.id}/reject/`, {
        reason: rejectReason,
      });
      setRejectFor(null);
      setRejectReason("");
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function restore() {
    if (!restoreFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${restoreFor.id}/restore/`);
      setRestoreFor(null);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function issue(payment: Payment) {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${payment.id}/issue/`);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  // Открыватели модалок: ошибка предыдущего действия не должна висеть в новой.
  function openRefund(row: Payment) {
    setError("");
    setRefundFor(row);
    setAmount(row.available_for_refund ?? "");
    setReason("");
  }
  function openReject(row: Payment) {
    setError("");
    setRejectFor(row);
    setRejectReason("");
  }
  function openRestore(row: Payment) {
    setError("");
    setRestoreFor(row);
  }
  function openStatus(row: Payment) {
    setStatusFor(row);
  }
  function closeStatus() {
    setStatusFor(null);
  }

  return {
    page,
    query,
    setQuery,
    statusFilter,
    setStatusFilter,
    department,
    setDepartment,
    data,
    loading,
    loadError,
    reload,
    rows,
    meta,
    statusItems,
    refreshFromStart,
    loadNextPage,
    busy,
    error,
    setError,
    receipt,
    issue,
    refund,
    reject,
    restore,
    refundFor,
    setRefundFor,
    statusFor,
    rejectFor,
    setRejectFor,
    restoreFor,
    setRestoreFor,
    qrFor,
    setQrFor,
    rejectReason,
    setRejectReason,
    amount,
    setAmount,
    reason,
    setReason,
    openRefund,
    openReject,
    openRestore,
    openStatus,
    closeStatus,
  };
}

export type Transactions = ReturnType<typeof useTransactions>;
```

- [ ] **Step 4: Действия** — создать `frontend/src/components/transactions/transaction-actions.tsx` (условия — один в один с иконками таблицы `transactions-section.tsx:536-615`):

```tsx
"use client";
import type { LucideIcon } from "lucide-react";
import { Download, ExternalLink, RotateCcw, Send, Undo2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Payment } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTIVE_PROVIDER_STATUSES = new Set(["creating", "processing", "pending", "cancelling"]);

export type TransactionActionKey = "restore" | "issue" | "qr" | "receipt" | "refund" | "reject";

export interface TransactionAction {
  key: TransactionActionKey;
  /** Подпись в списке (телефон) и на широких экранах у outline-кнопок. */
  label: string;
  /** Подсказка/имя для иконки без текста — как в таблице сейчас. */
  title: string;
  icon: LucideIcon;
  variant: "outline" | "ghost";
  destructive?: boolean;
  disabled?: boolean;
  run: () => void;
}

export interface TransactionActionHandlers {
  busy: boolean;
  receipt: (payment: Payment) => unknown;
  issue: (payment: Payment) => unknown;
  openRefund: (payment: Payment) => void;
  openReject: (payment: Payment) => void;
  openRestore: (payment: Payment) => void;
}

/** Какие действия доступны по операции — единый источник для таблицы и шторки. */
export function transactionActions(
  row: Payment,
  t: TransactionActionHandlers,
  perms: { canConfirm: boolean; canCreate: boolean },
): TransactionAction[] {
  const actions: TransactionAction[] = [];
  if (perms.canConfirm && row.can_restore) {
    actions.push({
      key: "restore",
      label: "Восстановить",
      title: "Восстановить отклонённую операцию",
      icon: Undo2,
      variant: "outline",
      run: () => t.openRestore(row),
    });
  }
  if (perms.canCreate && row.can_issue) {
    actions.push({
      key: "issue",
      label: "Отправить",
      title: "Отправить счёт клиенту",
      icon: Send,
      variant: "outline",
      disabled: t.busy,
      run: () => void t.issue(row),
    });
  }
  const qrUrl =
    row.provider?.channel === "qr" && row.provider.qr_token_url && ACTIVE_PROVIDER_STATUSES.has(row.provider.status)
      ? row.provider.qr_token_url
      : null;
  if (qrUrl) {
    actions.push({
      key: "qr",
      label: "Открыть Kaspi QR",
      title: "Открыть активный Kaspi QR",
      icon: ExternalLink,
      variant: "ghost",
      run: () => window.open(qrUrl, "_blank", "noopener"),
    });
  }
  if (row.status === "confirmed") {
    actions.push({
      key: "receipt",
      label: "Скачать выписку",
      title: "Скачать выписку ASYL LTD",
      icon: Download,
      variant: "ghost",
      run: () => void t.receipt(row),
    });
  }
  if (perms.canConfirm && row.status === "confirmed" && Number(row.available_for_refund ?? 0) > 0) {
    actions.push({
      key: "refund",
      label: "Вернуть оплату",
      title: row.provider ? "Вернуть через ApiPay" : "Вернуть деньги из кассы",
      icon: RotateCcw,
      variant: "ghost",
      run: () => t.openRefund(row),
    });
  }
  if (perms.canConfirm && ["requested", "received"].includes(row.status) && row.confirmation_mode !== "automatic") {
    actions.push({
      key: "reject",
      label: "Отклонить платёж",
      title: "Отклонить платёж",
      icon: XCircle,
      variant: "ghost",
      destructive: true,
      run: () => t.openReject(row),
    });
  }
  return actions;
}

/** `icons` — ряд иконок в ячейке таблицы (как сейчас), `list` — столбик широких кнопок в шторке. */
export function TransactionActions({ actions, layout }: { actions: TransactionAction[]; layout: "icons" | "list" }) {
  if (actions.length === 0) return null;
  if (layout === "list") {
    return (
      <div className="flex flex-col gap-2">
        {actions.map((action) => (
          <Button
            key={action.key}
            variant="outline"
            className={cn("h-11 justify-start", action.destructive && "text-[var(--destructive)]")}
            disabled={action.disabled}
            title={action.title}
            onClick={action.run}
          >
            <action.icon className="size-4" /> {action.label}
          </Button>
        ))}
      </div>
    );
  }
  return (
    <div className="flex justify-end gap-1">
      {actions.map((action) => (
        <Button
          key={action.key}
          size="sm"
          variant={action.variant}
          className={action.destructive ? "text-[var(--destructive)]" : undefined}
          disabled={action.disabled}
          title={action.title}
          onClick={action.run}
        >
          <action.icon className="size-4" />
          {action.variant === "outline" && <span className="hidden xl:inline">{action.label}</span>}
        </Button>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Детали и разбивка по способам** — создать `frontend/src/components/transactions/paid-method-summary.tsx`: перенести `PaidMethodSummary` из `transactions-section.tsx:97-118` с комментарием и добавить `export`; импорты: `import { PAYMENT_METHOD_LABELS } from "@/lib/constants"; import { formatCurrency } from "@/lib/utils";`.

Создать `frontend/src/components/transactions/transaction-detail.tsx`: перенести `STATUS_HELP` (строки 44–95) и `StatusExplanation` (120–142) без изменений и добавить компонент — это разметка тела окна статуса (строки 683–785) со `statusFor` → `payment`:

```tsx
import type { Payment } from "@/lib/types";
import { cn, currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";

// ...STATUS_HELP и StatusExplanation без изменений...

/** Содержимое окна «Статус операции»: сумма, смысл статуса, журнал операции, возвраты. */
export function TransactionDetail({ payment }: { payment: Payment }) {
  const steps = [
    {
      key: "created",
      label: "Создан",
      at: payment.paid_at,
      by: payment.recorded_by_name || "Система",
    },
    ...(payment.received_at
      ? [{ key: "received", label: "Принят кассой", at: payment.received_at, by: payment.received_by_name }]
      : []),
    ...(payment.confirmed_at
      ? [
          {
            key: "confirmed",
            label: payment.confirmation_mode === "automatic" ? "Подтверждён автоматически" : "Подтверждён",
            at: payment.confirmed_at,
            by: payment.confirmation_mode === "automatic" ? "Платёжный сервис" : payment.confirmed_by_name,
          },
        ]
      : []),
    ...(payment.status === "rejected" ? [{ key: "rejected", label: "Отклонён", at: null, by: null }] : []),
  ];
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-[var(--muted)]/35 p-4">
        <div className="text-sm font-medium">
          {payment.client_name} · заказ #{payment.order}
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">
          {formatMoney(payment.amount)} {currencySymbol(payment.currency)}
        </div>
        <div className="mt-2 space-y-0.5 text-xs text-[var(--muted-foreground)]">
          {payment.provider && (
            <div>
              Состояние счёта: {payment.provider.status}
              {payment.provider.phone_number ? ` · ${payment.provider.phone_number}` : ""}
            </div>
          )}
          {payment.note && <div>Примечание: {payment.note}</div>}
        </div>
      </div>
      <StatusExplanation status={payment.effective_status ?? payment.status} />
      {/* Журнал операции — для любого способа, включая наличные: раньше
          историю имели только онлайн-счета, и наличная транзакция
          выглядела безымянной. */}
      <div>
        <div className="mb-2 text-sm font-medium">Журнал операции</div>
        <div className="space-y-1.5">
          {steps.map((step) => (
            <div key={step.key} className="flex items-baseline gap-2 text-sm">
              <span
                className={cn(
                  "size-1.5 shrink-0 translate-y-[-1px] rounded-full",
                  step.key === "rejected" ? "bg-[var(--destructive)]" : "bg-[var(--success)]",
                )}
              />
              <span className="font-medium">{step.label}</span>
              <span className="text-xs text-[var(--muted-foreground)]">
                {step.at ? formatDateTime(step.at) : ""}
                {step.by ? ` · ${step.by}` : ""}
              </span>
            </div>
          ))}
        </div>
      </div>
      {(payment.refunds?.length ?? 0) > 0 && (
        <div>
          <div className="mb-2 text-sm font-medium">История возвратов</div>
          <div className="space-y-2">
            {payment.refunds!.map((refund) => (
              <div key={refund.id} className="rounded-lg border px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">
                    {formatMoney(refund.amount)} {currencySymbol(payment.currency)}
                  </span>
                  <span className="text-xs text-[var(--muted-foreground)]">
                    {refund.status === "completed" ? "Завершён" : refund.status === "pending" ? "В обработке" : "Ошибка"}
                  </span>
                </div>
                <div className="mt-1 text-xs text-[var(--muted-foreground)]">
                  {refund.method === "apipay" ? "По счёту" : "Из кассы"} · {refund.reason}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Модалки** — создать `frontend/src/components/transactions/transaction-modals.tsx`: перенести `PaymentQrPreview` (строки 146–186, с `Image`, `QrCode`, `ExternalLink`) и четыре модалки (635–848) с заменой локального состояния на `t.*`:

```tsx
"use client";
import Image from "next/image";
import { useState } from "react";
import { ExternalLink, QrCode } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { paymentStage } from "@/lib/constants";
import type { Payment } from "@/lib/types";
import { currencySymbol, formatMoney } from "@/lib/utils";
import { TransactionActions, transactionActions, type TransactionActionHandlers } from "./transaction-actions";
import { TransactionDetail } from "./transaction-detail";
import type { Transactions } from "./use-transactions";

// ...PaymentQrPreview без изменений...

const pad = (id: number | string) => String(id).padStart(6, "0");

/**
 * Возврат, статус, отклонение, восстановление, QR. `sheet` — окно статуса
 * шторкой (телефон); `detailActions` — в нём же список действий по операции.
 */
export function TransactionModals({
  t,
  canConfirm,
  canCreate,
  sheet = false,
  detailActions = false,
}: {
  t: Transactions;
  canConfirm: boolean;
  canCreate: boolean;
  sheet?: boolean;
  detailActions?: boolean;
}) {
  // Из шторки деталей действие сначала закрывает её, затем открывает свою модалку.
  const handlers: TransactionActionHandlers = {
    busy: t.busy,
    receipt: t.receipt,
    issue: (payment) => {
      t.closeStatus();
      return t.issue(payment);
    },
    openRefund: (payment) => {
      t.closeStatus();
      t.openRefund(payment);
    },
    openReject: (payment) => {
      t.closeStatus();
      t.openReject(payment);
    },
    openRestore: (payment) => {
      t.closeStatus();
      t.openRestore(payment);
    },
  };
  const actions = detailActions && t.statusFor ? transactionActions(t.statusFor, handlers, { canConfirm, canCreate }) : [];

  return (
    <>
      <Modal
        open={!!t.refundFor}
        onClose={() => !t.busy && t.setRefundFor(null)}
        eyebrow={t.refundFor?.provider ? "ApiPay · Возврат" : "Касса · Возврат"}
        title="Вернуть оплату"
        description={
          t.refundFor?.provider
            ? "Возврат будет отправлен через ApiPay. Деньги учтутся после подтверждения платёжного сервиса."
            : "Возврат будет сразу проведён как выдача денег из кассы и уменьшит оплаченную сумму заказа."
        }
        footer={
          <>
            <Button variant="outline" onClick={() => t.setRefundFor(null)}>
              Отмена
            </Button>
            <Button disabled={t.busy || !t.amount || !t.reason.trim()} onClick={() => void t.refund()}>
              {t.busy ? "Отправка…" : "Оформить возврат"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {t.error && <p className="text-sm text-[var(--destructive)]">{t.error}</p>}
          <div>
            <label className="mb-1.5 block text-sm">Сумма возврата</label>
            <Input type="number" min="0.01" step="0.01" value={t.amount} onChange={(e) => t.setAmount(e.target.value)} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина</label>
            <Input
              maxLength={500}
              placeholder="Например: возврат товара"
              value={t.reason}
              onChange={(e) => t.setReason(e.target.value)}
            />
          </div>
        </div>
      </Modal>

      <Modal
        open={!!t.statusFor}
        onClose={t.closeStatus}
        variant={sheet ? "sheet" : "dialog"}
        eyebrow="Статус операции"
        title={t.statusFor ? paymentStage(t.statusFor.effective_status ?? t.statusFor.status).label : "Статус"}
        description="Статус показывает, учитываются ли деньги в кассе и что можно сделать с операцией."
        footer={<Button onClick={t.closeStatus}>Понятно</Button>}
      >
        {t.statusFor && (
          <div className="space-y-4">
            <TransactionDetail payment={t.statusFor} />
            {actions.length > 0 && (
              <div>
                <div className="mb-2 text-sm font-medium">Действия</div>
                <TransactionActions actions={actions} layout="list" />
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={!!t.rejectFor}
        onClose={() => !t.busy && t.setRejectFor(null)}
        eyebrow="Касса · Контроль операции"
        title={`Отклонить PAY-${pad(t.rejectFor?.id ?? "")}?`}
        description="Платёж не будет учтён. Для телефонного счёта сначала будет запрошена отмена счёта на оплату."
        footer={
          <>
            <Button variant="outline" disabled={t.busy} onClick={() => t.setRejectFor(null)}>
              Не отклонять
            </Button>
            <Button variant="destructive" disabled={t.busy || !t.rejectReason.trim()} onClick={() => void t.reject()}>
              {t.busy ? "Отклонение…" : "Отклонить платёж"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {t.error && <p className="text-sm text-[var(--destructive)]">{t.error}</p>}
          <div className="rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3 text-sm">
            <div className="font-medium">{t.rejectFor?.client_name}</div>
            <div className="mt-1 text-[var(--muted-foreground)]">
              Заказ #{t.rejectFor?.order} · {formatMoney(t.rejectFor?.amount ?? 0)}{" "}
              {currencySymbol(t.rejectFor?.currency)}
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина отклонения</label>
            <Input
              autoFocus
              maxLength={500}
              placeholder="Например: ошибочно внесённая оплата"
              value={t.rejectReason}
              onChange={(event) => t.setRejectReason(event.target.value)}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!t.restoreFor}
        onClose={() => {
          if (!t.busy) {
            t.setRestoreFor(null);
            t.setError("");
          }
        }}
        title={`Восстановить PAY-${pad(t.restoreFor?.id ?? "")}?`}
        description={
          t.restoreFor?.method === "invoice"
            ? "Операция снова зарезервирует сумму заказа, после чего новый счёт будет отправлен клиенту."
            : "Операция вернётся в очередь кассира. Восстановление доступно только в пределах свободного остатка заказа."
        }
        confirmLabel="Восстановить"
        confirmVariant="default"
        busy={t.busy}
        error={t.error}
        onConfirm={() => void t.restore()}
      />

      {t.qrFor && <PaymentQrPreview payment={t.qrFor} onClose={() => t.setQrFor(null)} />}
    </>
  );
}
```

(`useState` нужен только внутри `PaymentQrPreview` — для `imageFailed`.)

- [ ] **Step 7: Десктопная секция поверх общих блоков** — заменить содержимое `frontend/src/components/transactions-section.tsx` на:

```tsx
"use client";

import { RefreshCcw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { TransactionActions, transactionActions } from "@/components/transactions/transaction-actions";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { paymentStage } from "@/lib/constants";
import type { Department } from "@/lib/types";
import { currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";

/* ── Вкладка «Транзакции» (десктоп): все платежи, возвраты и чеки ───────── */
export function TransactionsSection({
  onChanged,
  canConfirm,
  canCreate,
  departments,
}: {
  onChanged?: () => Promise<unknown>;
  canConfirm: boolean;
  canCreate: boolean;
  departments: Department[];
}) {
  const t = useTransactions({ onChanged });
  const { data, rows, meta, loading, loadError } = t;
  const perms = { canConfirm, canCreate };

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Операций</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{data?.count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Оплачено</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--success)]">
              {formatMoney(data?.summary.paid_by_currency.KZT ?? 0)} ₸
              {Number(data?.summary.paid_by_currency.USD ?? 0) > 0 && (
                <span className="ml-2 text-base text-[var(--muted-foreground)]">
                  + {formatMoney(data!.summary.paid_by_currency.USD)} $
                </span>
              )}
            </div>
            {/* Из чего сложился итог: касса сразу видит нал/QR/счёт. */}
            <PaidMethodSummary summary={data?.summary.paid_by_method} />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Возвращено</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">
              {formatMoney(data?.summary.refunded_by_currency.KZT ?? 0)} ₸
              {Number(data?.summary.refunded_by_currency.USD ?? 0) > 0 && (
                <span className="ml-2 text-base text-[var(--muted-foreground)]">
                  + {formatMoney(data!.summary.refunded_by_currency.USD)} $
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {t.error && !t.refundFor && !t.rejectFor && !t.restoreFor && (
        <div className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2 text-sm text-[var(--destructive)]">
          {t.error}
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>Все платежи, возвраты и чеки</CardTitle>
          <Button variant="outline" size="sm" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" /> Обновить
          </Button>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative max-w-sm flex-1 basis-64">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                className="pl-9"
                placeholder="Клиент, заказ или операция"
                value={t.query}
                onChange={(e) => t.setQuery(e.target.value)}
              />
            </div>
            <FilterDropdown
              label="Отдел"
              active={t.department}
              onChange={t.setDepartment}
              options={[{ key: "all", label: "Все" }, ...departments.map((row) => ({ key: row.code, label: row.name }))]}
            />
            {/* Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */}
            <div className="flex flex-wrap gap-1.5">
              {t.statusItems.map((item) => (
                <Chip key={item.key} active={t.statusFilter === item.key} onClick={() => t.setStatusFilter(item.key)}>
                  {item.label}
                  <span className="tabular-nums">{item.count}</span>
                </Chip>
              ))}
            </div>
          </div>
          {/* Спиннер на весь блок — только пока нет ни одной строки: догрузка
              следующих страниц не должна прятать уже показанное. */}
          {((loading && rows.length === 0) || loadError) && (
            <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={t.reload} />
          )}
          {!loading && !loadError && rows.length === 0 && (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
          )}
          {rows.length > 0 && (
            <>
              <Table>
                <THead>
                  <TR>
                    <TH>Операция</TH>
                    <TH>Клиент</TH>
                    <TH>Способ</TH>
                    <TH>Сумма</TH>
                    <TH>Статус</TH>
                    <TH>Возврат</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {rows.map((row) => {
                    const state = paymentStage(row.effective_status ?? row.status);
                    return (
                      <TR key={row.id}>
                        <TD>
                          <div className="font-medium">PAY-{String(row.id).padStart(6, "0")}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.paid_at)}</div>
                        </TD>
                        <TD>{row.client_name ?? "—"}</TD>
                        <TD>
                          {row.method_label ?? row.method}
                          {row.provider?.channel === "qr" && (
                            <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                          )}
                        </TD>
                        <TD className="font-medium tabular-nums">
                          {formatMoney(row.amount)} {currencySymbol(row.currency)}
                        </TD>
                        <TD>
                          <button
                            type="button"
                            onClick={() => t.openStatus(row)}
                            className="rounded-md outline-none ring-offset-2 hover:opacity-80 focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                            title="Нажмите, чтобы узнать значение статуса"
                          >
                            <Badge tone={state.tone}>{state.label}</Badge>
                          </button>
                        </TD>
                        <TD>
                          {Number(row.refunded_amount ?? 0) > 0 ? (
                            <span className="text-sm tabular-nums">
                              {formatMoney(row.refunded_amount ?? 0)} {currencySymbol(row.currency)}
                            </span>
                          ) : Number(row.pending_refund_amount ?? 0) > 0 ? (
                            <span className="text-sm text-[var(--warning)]">
                              {formatMoney(row.pending_refund_amount ?? 0)} в обработке
                            </span>
                          ) : (
                            "—"
                          )}
                        </TD>
                        <TD>
                          <TransactionActions layout="icons" actions={transactionActions(row, t, perms)} />
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
              <LoadMore
                shown={rows.length}
                total={meta?.count ?? rows.length}
                hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
                loading={loading && t.page > 1}
                onClick={t.loadNextPage}
              />
            </>
          )}
        </CardContent>
      </Card>

      <TransactionModals t={t} canConfirm={canConfirm} canCreate={canCreate} />
    </section>
  );
}
```

- [ ] **Step 8: Тесты зелёные, десктоп не изменился**

Run: `cd frontend && npx vitest run src/components/transactions src/components/transactions-section.test.tsx src/app/accounting && npx tsc --noEmit && npx eslint src/components/transactions src/components/transactions-section.tsx --max-warnings=0`
Expected: PASS. `transactions-section.tsx` больше не содержит `useState`/`api.post` — только композицию.

---

### Task 8: Мобильная оболочка — шторка фильтров, `MobileCashier`, главная, ветка в странице

**Files:**
- Create: `frontend/src/components/cashier/cash-filters-sheet.tsx`, `frontend/src/components/cashier/mobile/mobile-cashier.tsx`, `home-screen.tsx`, `mobile-cashier.test.tsx`
- Modify: `frontend/src/app/accounting/page.tsx`

**Interfaces:**
- Consumes: `useIsMobile` (Task 2), `Modal variant="sheet"`, `AppShell back/trailing` (Task 3), `NavList` (Task 4), `view.ts`/`filters.ts` (Task 5), `useCashier`/`CashierModel`, `CashFilterFields` (Task 6).
- Produces: `CashFiltersSheet({ open, onClose, filters, stores, departments, showRemaining, showDates?, onChange, onReset })`, `FilterButton({ count, onClick })`; `MobileCashier({ model })`, `SCREEN_TITLES`; `HomeScreen({ model, menu, onOpen })`. В `MobileCashier` — переключатель экранов, куда Tasks 9–11 добавляют по одной строке.

- [ ] **Step 1: Падающий тест мобильной кассы** — создать `frontend/src/components/cashier/mobile/mobile-cashier.test.tsx` (мок API в стиле `page.test.tsx`; данные общие для Tasks 8–11):

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, expect, it, vi } from "vitest";
import CashierPage from "@/app/accounting/page";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";
import type { TopbarBack } from "@/components/layout/topbar";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: { is_superuser: true, permissions: [] as string[] },
  byMethod: true,
  pendingCount: 0,
}));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: () => {} }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({
    title,
    back,
    trailing,
    children,
  }: {
    title: string;
    back?: TopbarBack;
    trailing?: React.ReactNode;
    children: React.ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {back && (
        <button type="button" onClick={back.onClick}>
          {back.label}
        </button>
      )}
      {trailing}
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

const today = new Date();
const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const queueItem = { id: 1, order: 1, amount: "100", currency: "KZT", method: "cash", status: "received", client_name: "Клиент" };
const transaction = {
  id: 5,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  method_label: "Наличные",
  status: "confirmed",
  effective_status: "confirmed",
  paid_at: `${todayIso}T10:00:00`,
  recorded_by: null,
  client_name: "Клиент",
  available_for_refund: "100",
};
const logEvent = {
  id: 1,
  message: "Оплата подтверждена",
  user_name: "Касса",
  order: 1,
  client_name: "Клиент",
  store_name: null,
  payload: { payment_id: 1 },
  created_at: `${todayIso}T09:00:00`,
  can_reopen: true,
  can_restore: false,
};

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
});

beforeEach(() => {
  resetNavigation("/accounting");
  mocks.me = { is_superuser: true, permissions: [] };
  mocks.byMethod = true;
  mocks.pendingCount = 0;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/reports/summary/") {
      return {
        data: {
          from: url.searchParams.get("from"),
          to: url.searchParams.get("to"),
          income: {
            total: "100",
            cash: "100",
            cashless: "0",
            gross: "100",
            refunded: "0",
            payments: 1,
            refunds: 0,
            currency: "KZT",
            by_currency: { KZT: "100" },
            cash_by_currency: { KZT: "100" },
            cashless_by_currency: { KZT: "0" },
            gross_by_currency: { KZT: "100" },
            refunded_by_currency: {},
            ...(mocks.byMethod ? { by_method_by_currency: { KZT: { cash: "100" } }, payments_by_method: { cash: 1 } } : {}),
          },
          departments: [
            {
              code: "main",
              name: "Мельница",
              color: "#123456",
              orders: null,
              sales_by_currency: null,
              received_by_currency: { KZT: "100" },
              refunded_by_currency: {},
              net_by_currency: { KZT: "100" },
              payments: 1,
            },
          ],
        },
      };
    }
    if (url.pathname === "/orders/payments-queue/") {
      if (url.searchParams.get("summary") === "1")
        return { data: [{ currency: "KZT", method: "cash", amount: "100", count: 1 }] };
      return { data: { results: [queueItem], count: 1, next: null } };
    }
    if (url.pathname === "/orders/") return { data: { results: [], count: mocks.pendingCount, next: null } };
    if (url.pathname === "/orders/cashier-log/") return { data: { results: [logEvent], count: 1, next: null } };
    if (url.pathname === "/clients/debts/")
      return {
        data: [
          {
            client_id: 1,
            client_name: "Клиент",
            client_phone: "+7 700 000 00 00",
            debt_total: "100",
            debt_currency: "KZT",
            debt_by_currency: { KZT: "100" },
            unpaid_count: 1,
            partial_count: 0,
            orders_count: 1,
            stores_count: 0,
            overdue_count: 0,
          },
        ],
      };
    if (url.pathname === "/payment-transactions/")
      return {
        data: {
          results: [transaction],
          page: 1,
          pages: 1,
          count: 1,
          status_counts: { confirmed: 1 },
          summary: {
            paid_by_currency: { KZT: "100", USD: "0" },
            refunded_by_currency: { KZT: "0", USD: "0" },
            paid_by_method: { KZT: { cash: "100" } },
          },
        },
      };
    return { data: [] };
  });
});

it("shows the home menu with live subtitles and opens a section by pushing ?view=", async () => {
  const user = userEvent.setup();
  mocks.pendingCount = 2;
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Касса" })).toBeInTheDocument();
  const menu = within(screen.getByRole("navigation", { name: "Разделы кассы" }));
  await waitFor(() => expect(menu.getByText("2 заявки · 1 оплата на 100 ₸")).toBeInTheDocument());
  expect(menu.getByText("1 клиент · 100 ₸")).toBeInTheDocument();
  expect(menu.getAllByRole("button").map((b) => b.textContent)).toEqual([
    expect.stringContaining("Заявки и оплаты"),
    expect.stringContaining("Долги клиентов"),
    expect.stringContaining("Транзакции"),
    expect.stringContaining("Журнал"),
    expect.stringContaining("Отчёт по поступлениям"),
  ]);
  const headline = await screen.findByRole("button", { name: /Поступления за сегодня/ });
  expect(headline).toHaveTextContent("100 ₸");
  expect(mocks.get.mock.calls.some(([url]) => url === `/reports/summary/?section=income&from=${todayIso}&to=${todayIso}`)).toBe(true);

  await user.click(menu.getByRole("button", { name: /Заявки и оплаты/ }));
  expect(routerCalls.push).toEqual(["/accounting?view=confirm"]);
  expect(await screen.findByRole("heading", { name: "Заявки и оплаты" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(1);
  expect(await screen.findByRole("heading", { name: "Касса" })).toBeInTheDocument();
});

it("opens the only available section directly, without a home screen", async () => {
  mocks.me = { is_superuser: false, permissions: ["payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Транзакции" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Назад в кассу" })).not.toBeInTheDocument();
});

it("returns to the home screen by replace when a sub-screen was opened by link", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(0);
  expect(routerCalls.replace).toEqual(["/accounting"]);
});
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd frontend && npx vitest run src/components/cashier/mobile`
Expected: FAIL — на телефоне рендерится десктоп (`mobile = false`), заголовка «Касса» из мока AppShell нет либо нет навигации «Разделы кассы».

- [ ] **Step 3: Шторка фильтров и кнопка** — создать `frontend/src/components/cashier/cash-filters-sheet.tsx`:

```tsx
"use client";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import type { Department, Store } from "@/lib/types";
import { CashFilterFields } from "./cash-filter-fields";
import { activeFilterCount, type CashFilters } from "./filters";

/** Иконка фильтров в топбаре подэкрана; бейдж — сколько групп задано. */
export function FilterButton({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={count ? `Фильтры, применено: ${count}` : "Фильтры"}
      className="relative flex size-8 items-center justify-center rounded-lg border text-[var(--foreground)]"
    >
      <SlidersHorizontal className="size-4" />
      {count > 0 && (
        <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--foreground)] px-1 text-[10px] font-semibold tabular-nums text-[var(--background)]">
          {count}
        </span>
      )}
    </button>
  );
}

/** Те же поля, что в десктопной панели, но шторкой снизу; изменения применяются сразу. */
export function CashFiltersSheet({
  open,
  onClose,
  filters,
  stores,
  departments,
  showRemaining,
  showDates = true,
  onChange,
  onReset,
}: {
  open: boolean;
  onClose: () => void;
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  showRemaining: boolean;
  showDates?: boolean;
  onChange: (patch: Partial<CashFilters>) => void;
  onReset: () => void;
}) {
  const count = activeFilterCount(filters, { dates: showDates, remaining: showRemaining });
  return (
    <Modal
      variant="sheet"
      open={open}
      onClose={onClose}
      eyebrow="Касса"
      title="Фильтры"
      description={count ? `Применено: ${count}` : "Без ограничений"}
      footer={
        <>
          <Button variant="ghost" disabled={!count} onClick={onReset}>
            <X className="size-4" /> Сбросить
          </Button>
          <Button onClick={onClose}>Готово</Button>
        </>
      }
    >
      <CashFilterFields
        layout="stack"
        filters={filters}
        stores={stores}
        departments={departments}
        showRemaining={showRemaining}
        showDates={showDates}
        onChange={onChange}
      />
    </Modal>
  );
}
```

- [ ] **Step 4: Главная** — создать `frontend/src/components/cashier/mobile/home-screen.tsx`:

```tsx
"use client";
import { ChartPie, ChevronRight, HandCoins, History, Receipt, Users } from "lucide-react";
import { ErrorAlert } from "@/components/ui/data-state";
import { NavList } from "@/components/ui/nav-list";
import { cn, formatCompactCurrency, formatCurrency, pluralRu } from "@/lib/utils";
import type { CashierModel } from "../use-cashier";
import type { MobileMenuKey } from "../view";

const ITEMS: Record<MobileMenuKey, { title: string; icon: React.ElementType; hint: string }> = {
  confirm: { title: "Заявки и оплаты", icon: HandCoins, hint: "Очередь подтверждения" },
  debts: { title: "Долги клиентов", icon: Users, hint: "Остатки по клиентам" },
  transactions: { title: "Транзакции", icon: Receipt, hint: "Все платежи, возвраты и чеки" },
  journal: { title: "Журнал", icon: History, hint: "Действия по оплатам" },
  report: { title: "Отчёт по поступлениям", icon: ChartPie, hint: "По отделам и способам оплаты" },
};

function confirmSubtitle(model: CashierModel): string {
  if (!model.queueReady) return ITEMS.confirm.hint;
  const parts: string[] = [];
  const pending = model.pendingCount;
  if (model.perms.canReviewOrders && !pending.loading && !pending.error && pending.count > 0) {
    parts.push(`${pending.count} ${pluralRu(pending.count, ["заявка", "заявки", "заявок"])}`);
  }
  const { count, total, currency } = model.queueTotals;
  if (count > 0) {
    parts.push(`${count} ${pluralRu(count, ["оплата", "оплаты", "оплат"])} на ${formatCompactCurrency(total, currency)}`);
  }
  return parts.length ? parts.join(" · ") : "Очередь пуста";
}

function debtsSubtitle(model: CashierModel): string {
  if (!model.debtsReady) return ITEMS.debts.hint;
  const { clients, total, currency, other, overdue } = model.debtTotals;
  if (clients === 0) return "Долгов нет";
  // Валюты не складываются: «1,2 млн ₸ + 500 $».
  const amount = [formatCompactCurrency(total, currency), ...other.map(([unit, value]) => formatCompactCurrency(value, unit))].join(
    " + ",
  );
  return [
    `${clients} ${pluralRu(clients, ["клиент", "клиента", "клиентов"])}`,
    amount,
    overdue > 0 ? `${overdue} с просрочкой` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Главная кассы на телефоне: карточка-сводка сверху и список разделов с живыми цифрами. */
export function HomeScreen({
  model,
  menu,
  onOpen,
}: {
  model: CashierModel;
  menu: MobileMenuKey[];
  onOpen: (view: MobileMenuKey) => void;
}) {
  const { perms, income, incomeReady, queueTotals, queueReady, summary, queueSummary, debts } = model;
  const headline = perms.canReports
    ? {
        title: "Поступления за сегодня",
        caption: "чистыми, с учётом возвратов",
        value: incomeReady ? formatCurrency(income.total, income.currency) : "—",
        negative: incomeReady && income.total < 0,
        target: "report" as const,
      }
    : perms.canPayments
      ? {
          title: "Ожидает подтверждения",
          caption: queueReady ? `${queueTotals.count} ${pluralRu(queueTotals.count, ["оплата", "оплаты", "оплат"])} в очереди` : "",
          value: queueReady ? formatCurrency(queueTotals.total, queueTotals.currency) : "—",
          negative: false,
          target: "confirm" as const,
        }
      : null;
  const subtitles: Record<MobileMenuKey, string> = {
    confirm: confirmSubtitle(model),
    debts: debtsSubtitle(model),
    transactions: ITEMS.transactions.hint,
    journal: ITEMS.journal.hint,
    report: ITEMS.report.hint,
  };
  const loadError = summary.error || queueSummary.error || debts.error;

  return (
    <div className="flex flex-col gap-4">
      {headline && (
        <button
          type="button"
          onClick={() => onOpen(headline.target)}
          className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-3.5 text-left shadow-card transition-colors hover:bg-[var(--muted)]/60"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium">{headline.title}</span>
            {headline.caption && (
              <span className="block text-[13px] text-[var(--muted-foreground)]">{headline.caption}</span>
            )}
          </span>
          <span className={cn("text-[17px] font-bold tabular-nums", headline.negative && "text-[var(--destructive)]")}>
            {headline.value}
          </span>
          <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
        </button>
      )}
      {loadError && <ErrorAlert message={loadError} onRetry={model.reloadOverview} />}
      <NavList
        label="Разделы кассы"
        items={menu.map((key) => ({
          key,
          icon: ITEMS[key].icon,
          title: ITEMS[key].title,
          subtitle: subtitles[key],
          onSelect: () => onOpen(key),
        }))}
      />
    </div>
  );
}
```

- [ ] **Step 5: `MobileCashier`** — создать `frontend/src/components/cashier/mobile/mobile-cashier.tsx`:

```tsx
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { CashFiltersSheet, FilterButton } from "../cash-filters-sheet";
import { activeFilterCount } from "../filters";
import type { CashierModel } from "../use-cashier";
import { mobileMenu, type CashView, type MobileMenuKey } from "../view";
import { HomeScreen } from "./home-screen";

export const SCREEN_TITLES: Record<CashView, string> = {
  home: "Касса",
  overview: "Касса",
  report: "Отчёт по поступлениям",
  debts: "Долги клиентов",
  confirm: "Заявки и оплаты",
  journal: "Журнал",
  transactions: "Транзакции",
};

/** Касса на телефоне: главная-меню и подэкраны с «‹ назад» вместо «☰». */
export function MobileCashier({ model }: { model: CashierModel }) {
  const router = useRouter();
  const pathname = usePathname();
  const { view, perms, filterScreen } = model;
  const menu = mobileMenu(perms);
  // «‹» возвращает историей, только если экран открыт отсюда; по диплинку — заменяем адрес на главную.
  const cameFromHome = useRef(false);
  useEffect(() => {
    if (view === "home") cameFromHome.current = false;
  }, [view]);
  const open = useCallback(
    (next: MobileMenuKey) => {
      cameFromHome.current = true;
      router.push(`${pathname}?view=${next}`);
    },
    [pathname, router],
  );
  const back = useCallback(() => {
    if (cameFromHome.current) router.back();
    else router.replace(pathname);
  }, [pathname, router]);

  const [filtersOpen, setFiltersOpen] = useState(false);
  const showBack = view !== "home" && menu.length > 1;
  const activeFilters = filterScreen
    ? activeFilterCount(model.filters, { dates: filterScreen !== "report", remaining: filterScreen === "debts" })
    : 0;

  return (
    <AppShell
      title={SCREEN_TITLES[view]}
      section={view === "home" ? "Работа" : "Касса"}
      back={showBack ? { label: "Назад в кассу", onClick: back } : undefined}
      trailing={filterScreen ? <FilterButton count={activeFilters} onClick={() => setFiltersOpen(true)} /> : undefined}
    >
      {filterScreen && (
        <CashFiltersSheet
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          filters={model.filters}
          stores={model.stores}
          departments={model.departments}
          showRemaining={filterScreen === "debts"}
          showDates={filterScreen !== "report"}
          onChange={model.patchFilters}
          onReset={model.resetFilters}
        />
      )}
      {view === "home" && <HomeScreen model={model} menu={menu} onOpen={open} />}
    </AppShell>
  );
}
```

- [ ] **Step 6: Ветка в странице** — в `frontend/src/app/accounting/page.tsx`:
  - импорты: добавить `import { MobileCashier } from "@/components/cashier/mobile/mobile-cashier";` и `import { useIsMobile } from "@/lib/use-media-query";`
  - строки `// Мобильная раскладка подключается следующим шагом плана.` и `const mobile = false;` заменить на `const mobile = useIsMobile();`
  - `return <CashierDesktop model={model} onTab={selectTab} />;` заменить на:

```tsx
  if (mobile) return <MobileCashier model={model} />;
  return <CashierDesktop model={model} onTab={selectTab} />;
```

- [ ] **Step 7: Тесты**

Run: `cd frontend && npx vitest run src/components/cashier src/app/accounting && npx tsc --noEmit`
Expected: три сценария `mobile-cashier.test.tsx` PASS (первый — до заголовка «Заявки и оплаты» и возврата; сам экран очереди появится в Task 9, поэтому после перехода в тесте проверяется только заголовок); `page.test.tsx` по-прежнему PASS (в нём `matchMedia` отсутствует → десктоп).

---

### Task 9: Экраны «Заявки и оплаты» и «Журнал»

**Files:**
- Create: `frontend/src/components/cashier/mobile/confirm-screen.tsx`, `journal-screen.tsx`
- Modify: `frontend/src/components/cashier/mobile/mobile-cashier.tsx` (импорт + две строки в переключателе), `mobile-cashier.test.tsx` (два сценария)

**Interfaces:**
- Consumes: `CashierModel` (`queue: CashierQueue`, `journalLog: PagedCashierLog`, `perms`), `OrderReviewDialogs`, `RestorePaymentDialog`, `ActionError`, `DepartmentBadge` (Task 6/5), существующие `groupByDay` (`@/lib/day-groups`) и `useLocalDay` (`@/lib/use-local-day`), `Tabs variant="segment"`, `PaymentStageBadge`, `LoadMore`, `ErrorAlert`.
- Produces: `ConfirmScreen({ model })`, `JournalScreen({ model })`.

- [ ] **Step 1: Падающие тесты** — в `mobile-cashier.test.tsx` добавить:

```tsx
it("confirms a payment from the queue screen", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  render(<CashierPage />);
  expect(await screen.findByRole("tab", { name: /Оплаты/ })).toHaveAttribute("aria-selected", "true");
  await user.click(await screen.findByRole("button", { name: "Подтвердить получение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/confirm/"));
  await user.click(screen.getByRole("tab", { name: /Заявки/ }));
  expect(await screen.findByText("Нет заявок, ожидающих подтверждения.")).toBeInTheDocument();
});

it("groups the journal by day and reopens a payment", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=journal");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
  expect(screen.getByText("Оплата подтверждена")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Вернуть на подтверждение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/reopen/"));
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/cashier/mobile`
Expected: FAIL — на экранах пусто (нет вкладки «Оплаты», нет заголовка «Сегодня»).

- [ ] **Step 3: Экран очереди** — создать `frontend/src/components/cashier/mobile/confirm-screen.tsx`:

```tsx
"use client";
import Link from "next/link";
import { useState } from "react";
import { PaymentStageBadge } from "@/components/payment-chain";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import type { Order, PaymentQueueItem } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";
import { ActionError } from "../action-error";
import { DepartmentBadge } from "../department-badge";
import { OrderReviewDialogs } from "../order-review-dialogs";
import type { CashierModel } from "../use-cashier";
import type { CashierQueue } from "../use-cashier-queue";

const LIST_CLASS = "divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";

function PaymentRow({
  p,
  q,
  canViewOrders,
  canReceive,
}: {
  p: PaymentQueueItem;
  q: CashierQueue;
  canViewOrders: boolean;
  canReceive: boolean;
}) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[17px] font-bold tabular-nums">{formatCurrency(p.amount, p.currency ?? "KZT")}</div>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {canViewOrders ? (
              <Link href={`/orders/${p.order}`} className="underline-offset-2 hover:underline">
                Заказ #{p.order}
              </Link>
            ) : (
              <span>Заказ #{p.order}</span>
            )}
            {" · "}
            {p.client_name} · {PAYMENT_METHOD_LABELS[p.method] ?? p.method_label}
            {p.store_name ? ` · ${p.store_name}` : ""}
            {p.received_by_name ? ` · принял ${p.received_by_name}` : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <PaymentStageBadge status={p.status} />
          <DepartmentBadge name={p.department_name} color={p.department_color} />
        </div>
      </div>
      <div className="flex gap-2">
        {p.status !== "requested" || canReceive ? (
          <Button
            className="flex-1"
            disabled={q.busy}
            onClick={() => (p.status === "requested" ? q.receivePayment(p) : q.confirmPayment(p))}
          >
            {p.status === "requested" ? "Оплата поступила" : "Подтвердить получение"}
          </Button>
        ) : (
          <p className="flex-1 self-center text-xs text-[var(--muted-foreground)]">
            Принять может сотрудник с правом подтверждения оплат.
          </p>
        )}
        <Button variant="ghost" disabled={q.busy} onClick={() => q.rejectPayment(p)}>
          Отклонить
        </Button>
      </div>
    </li>
  );
}

function RequestRow({ o, q, onConfirm, onReject }: { o: Order; q: CashierQueue; onConfirm: () => void; onReject: () => void }) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/orders/${o.id}`} className="text-[15px] font-semibold underline-offset-2 hover:underline">
            Заказ #{o.id}
          </Link>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {o.client_name} · {formatCurrency(o.total_amount, o.currency)}
          </div>
        </div>
        <DepartmentBadge
          name={o.department ? o.department_name || o.department : "Нет отдела"}
          color={o.department ? o.department_color : undefined}
        />
      </div>
      <div className="flex gap-2">
        <Button className="flex-1" disabled={q.busy} onClick={onConfirm}>
          Проверить и подтвердить
        </Button>
        {o.status === "pending" && (
          <Button variant="outline" disabled={q.busy} onClick={onReject}>
            Отклонить
          </Button>
        )}
      </div>
    </li>
  );
}

/** Очередь на телефоне: сегмент «Оплаты / Заявки», действия прямо в строках. */
export function ConfirmScreen({ model }: { model: CashierModel }) {
  const { queue: q, perms } = model;
  const [segment, setSegment] = useState<"payments" | "requests">("payments");
  const [confirming, setConfirming] = useState<Order | null>(null);
  const [rejecting, setRejecting] = useState<Order | null>(null);
  const showRequests = perms.canReviewOrders && segment === "requests";
  const tabs: TabDef[] = [
    { key: "payments", label: "Оплаты", count: q.loading ? undefined : q.queuePage.count },
    ...(perms.canReviewOrders
      ? [{ key: "requests", label: "Заявки", count: q.loading ? undefined : q.pendingPage.count }]
      : []),
  ];
  const empty = (text: string) => <p className="px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{text}</p>;

  return (
    <section className="flex flex-col gap-4">
      <OrderReviewDialogs
        q={q}
        confirming={confirming}
        rejecting={rejecting}
        onConfirmClose={() => setConfirming(null)}
        onRejectClose={() => setRejecting(null)}
      />
      {perms.canReviewOrders && (
        <Tabs
          variant="segment"
          label="Очередь"
          className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
          tabs={tabs}
          active={segment}
          onChange={(key) => setSegment(key as "payments" | "requests")}
        />
      )}
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}
      <div className={LIST_CLASS}>
        {q.loading && (showRequests ? q.pendingOrders : q.toReview).length === 0 ? (
          empty("Загрузка…")
        ) : showRequests ? (
          !q.loadError && q.pendingOrders.length === 0 ? (
            empty("Нет заявок, ожидающих подтверждения.")
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {q.pendingOrders.map((o) => (
                <RequestRow key={o.id} o={o} q={q} onConfirm={() => setConfirming(o)} onReject={() => setRejecting(o)} />
              ))}
            </ul>
          )
        ) : !q.loadError && q.toReview.length === 0 ? (
          empty("Нет оплат, ожидающих подтверждения.")
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {q.toReview.map((p) => (
              <PaymentRow key={p.id} p={p} q={q} canViewOrders={perms.canViewOrders} canReceive={perms.canPayments} />
            ))}
          </ul>
        )}
      </div>
      {showRequests ? (
        <LoadMore
          shown={q.pendingOrders.length}
          total={q.pendingPage.count}
          hasMore={q.pendingPage.hasMore}
          loading={q.pendingPage.loadingMore}
          onClick={q.pendingPage.loadMore}
        />
      ) : (
        <LoadMore
          shown={q.toReview.length}
          total={q.queuePage.count}
          hasMore={q.queuePage.hasMore}
          loading={q.queuePage.loadingMore}
          onClick={q.queuePage.loadMore}
        />
      )}
    </section>
  );
}
```

- [ ] **Step 4: Экран журнала** — создать `frontend/src/components/cashier/mobile/journal-screen.tsx`:

```tsx
"use client";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { groupByDay } from "@/lib/day-groups";
import type { CashierLogItem } from "@/lib/types";
import { useLocalDay } from "@/lib/use-local-day";
import { formatTime } from "@/lib/utils";
import { ActionError } from "../action-error";
import { RestorePaymentDialog } from "../restore-payment-dialog";
import type { CashierModel } from "../use-cashier";

/** Журнал действий по оплатам: лента по дням, как история операций в банке. */
export function JournalScreen({ model }: { model: CashierModel }) {
  const { journalLog: log, queue: q } = model;
  const [restoreEvent, setRestoreEvent] = useState<CashierLogItem | null>(null);
  const currentDay = useLocalDay();
  const groups = groupByDay(log.items, (event) => new Date(event.created_at), currentDay);

  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {log.error && <ErrorAlert message={log.error} onRetry={log.reload} />}
      {log.loading && log.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : !log.error && log.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Действий по оплатам пока нет.</p>
      ) : (
        groups.map((group) => (
          <section key={group.key} className="flex flex-col gap-2">
            <h2 className="px-1 text-[15px] font-semibold">{group.label}</h2>
            <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
              {group.items.map((event) => (
                <li key={event.id} className="flex flex-col gap-2 px-4 py-3">
                  <div className="text-sm font-medium">{event.message}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {formatTime(event.created_at)}
                    {` · заказ #${event.order}`}
                    {event.client_name ? ` · ${event.client_name}` : ""}
                    {event.user_name ? ` · ${event.user_name}` : ""}
                  </div>
                  {(event.can_reopen || event.can_restore) && (
                    <div className="flex flex-wrap gap-2">
                      {event.can_reopen && (
                        <Button size="sm" variant="outline" disabled={q.busy} onClick={() => q.reopenPayment(event)}>
                          Вернуть на подтверждение
                        </Button>
                      )}
                      {event.can_restore && (
                        <Button size="sm" variant="outline" disabled={q.busy} onClick={() => setRestoreEvent(event)}>
                          <RefreshCw className="size-3.5" /> Восстановить
                        </Button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
      <LoadMore shown={log.items.length} total={log.count} hasMore={log.hasMore} loading={log.loadingMore} onClick={log.loadMore} />
      <RestorePaymentDialog q={q} event={restoreEvent} onClose={() => setRestoreEvent(null)} />
    </section>
  );
}
```

- [ ] **Step 5: Подключить экраны** — в `mobile-cashier.tsx` добавить импорты `import { ConfirmScreen } from "./confirm-screen"; import { JournalScreen } from "./journal-screen";` и после строки `{view === "home" && ...}` добавить:

```tsx
      {view === "confirm" && <ConfirmScreen model={model} />}
      {view === "journal" && <JournalScreen model={model} />}
```

- [ ] **Step 6: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier/mobile && npx tsc --noEmit`
Expected: PASS.

---

### Task 10: Экраны «Долги» и «Отчёт»

**Files:**
- Create: `frontend/src/components/cashier/mobile/debts-screen.tsx`, `report-segments.ts`, `report-segments.test.ts`, `report-screen.tsx`
- Modify: `mobile-cashier.tsx` (импорт + две строки), `mobile-cashier.test.tsx` (два сценария)

**Interfaces:**
- Consumes: `DonutChart`, `Chip` (Task 4), `PERIOD_PRESETS`/`periodRange`/`periodPresetOf` (Task 5), `IncomeSummary` (Task 5), `useOverdueCheck`, `matchesDebtQuery`, `debtPaymentState` (Task 6/5), `ActionCard`, `CurrencyAmounts`, `Badge`, `Modal variant="sheet"`.
- Produces: `DebtsScreen({ model })`, `ReportScreen({ model })`, `reportSegments(summary, breakdown, currency): { segments, counts, fallback }`, `Breakdown`, `METHOD_COLORS`.

- [ ] **Step 1: Падающие тесты** — создать `frontend/src/components/cashier/mobile/report-segments.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { IncomeSummary } from "@/components/cashier/totals";
import { reportSegments } from "./report-segments";

const income: IncomeSummary["income"] = {
  total: "400",
  cash: "300",
  cashless: "100",
  gross: "400",
  refunded: "0",
  payments: 3,
  refunds: 0,
  currency: "KZT",
  by_currency: { KZT: "400" },
  cash_by_currency: { KZT: "300" },
  cashless_by_currency: { KZT: "100" },
  gross_by_currency: { KZT: "400" },
  refunded_by_currency: {},
};
const summary: IncomeSummary = {
  from: null,
  to: null,
  income: { ...income, by_method_by_currency: { KZT: { kaspi: "100", cash: "300" } }, payments_by_method: { cash: 2, kaspi: 1 } },
  departments: [
    {
      code: "main",
      name: "Мельница",
      color: "#111",
      orders: null,
      sales_by_currency: null,
      received_by_currency: { KZT: "400" },
      refunded_by_currency: {},
      net_by_currency: { KZT: "400" },
      payments: 3,
    },
  ],
};

describe("reportSegments", () => {
  it("splits by department with payment counts", () => {
    const result = reportSegments(summary, "departments", "KZT");
    expect(result.segments).toEqual([{ key: "main", label: "Мельница", value: 400, color: "#111" }]);
    expect(result.counts).toEqual({ main: 3 });
    expect(result.fallback).toBe(false);
  });
  it("splits by method, largest first, with method labels", () => {
    const result = reportSegments(summary, "methods", "KZT");
    expect(result.segments.map((s) => [s.key, s.label, s.value])).toEqual([
      ["cash", "Наличные", 300],
      ["kaspi", "QR", 100],
    ]);
    expect(result.counts).toEqual({ cash: 2, kaspi: 1 });
  });
  it("falls back to cash/cashless for an older backend", () => {
    const result = reportSegments({ ...summary, income }, "methods", "KZT");
    expect(result.fallback).toBe(true);
    expect(result.segments.map((s) => [s.label, s.value])).toEqual([
      ["Наличные", 300],
      ["Безналичные", 100],
    ]);
  });
  it("is empty without data", () => {
    expect(reportSegments(null, "departments", "KZT")).toEqual({ segments: [], counts: {}, fallback: false });
  });
});
```

в `mobile-cashier.test.tsx` добавить:

```tsx
it("lists debtors as tappable rows with a totals line", async () => {
  resetNavigation("/accounting?view=debts");
  render(<CashierPage />);
  const row = await screen.findByRole("link", { name: "Долг клиента Клиент" });
  expect(row).toHaveAttribute("href", "/accounting/debts/clients/1");
  expect(screen.getByText(/1 клиент/)).toBeInTheDocument();
  expect(screen.getByText("Не оплачен")).toBeInTheDocument();
});

it("draws the report for today and switches the breakdown", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  expect(await screen.findByRole("button", { name: "Сегодня", pressed: true })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("img", { name: /Мельница: 100%/ })).toBeInTheDocument());
  await user.click(screen.getByRole("tab", { name: "По способу" }));
  expect(screen.getByRole("img", { name: /Наличные: 100%/ })).toBeInTheDocument();
  expect(screen.getByText("1 оплата")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Всё" }));
  await waitFor(() => expect(mocks.get.mock.calls.some(([url]) => url === "/reports/summary/?section=income")).toBe(true));
});

it("shows cash and cashless when the backend has no method breakdown", async () => {
  const user = userEvent.setup();
  mocks.byMethod = false;
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  await user.click(await screen.findByRole("tab", { name: "По способу" }));
  await waitFor(() => expect(screen.getByText("Безналичные")).toBeInTheDocument());
});
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd frontend && npx vitest run src/components/cashier/mobile`
Expected: FAIL.

- [ ] **Step 3: Сегменты отчёта** — создать `frontend/src/components/cashier/mobile/report-segments.ts`:

```ts
import type { IncomeSummary } from "@/components/cashier/totals";
import type { DonutSegment } from "@/components/ui/donut-chart";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { amountForCurrency, finiteMoney } from "@/lib/currency-map";

export type Breakdown = "departments" | "methods";

/** Цвета способов — токены темы, чтобы кольцо читалось и в тёмной теме. */
export const METHOD_COLORS: Record<string, string> = {
  cash: "var(--success)",
  kaspi: "var(--ring)",
  invoice: "var(--warning)",
  card: "var(--muted-foreground)",
};

export interface ReportBreakdown {
  segments: DonutSegment[];
  /** Число оплат по ключу сегмента, если бэкенд его отдаёт. */
  counts: Record<string, number>;
  /** true — старый бэкенд без by_method: показаны только «Наличные/Безналичные». */
  fallback: boolean;
}

const byValueDesc = (a: DonutSegment, b: DonutSegment) => b.value - a.value;

export function reportSegments(summary: IncomeSummary | null, breakdown: Breakdown, currency: string): ReportBreakdown {
  if (!summary) return { segments: [], counts: {}, fallback: false };
  if (breakdown === "departments") {
    const counts: Record<string, number> = {};
    const segments = (summary.departments ?? []).map((row) => {
      if (row.payments !== undefined) counts[row.code] = row.payments;
      return { key: row.code, label: row.name, value: finiteMoney(row.net_by_currency[currency] ?? 0), color: row.color };
    });
    return { segments: segments.sort(byValueDesc), counts, fallback: false };
  }
  const byMethod = summary.income.by_method_by_currency;
  if (byMethod) {
    const segments = Object.entries(byMethod[currency] ?? {}).map(([method, value]) => ({
      key: method,
      label: PAYMENT_METHOD_LABELS[method] ?? method,
      value: finiteMoney(value),
      color: METHOD_COLORS[method] ?? "var(--muted-foreground)",
    }));
    return { segments: segments.sort(byValueDesc), counts: summary.income.payments_by_method ?? {}, fallback: false };
  }
  // Старый бэкенд без разбивки по способам: показываем хотя бы нал/безнал.
  return {
    segments: [
      {
        key: "cash",
        label: "Наличные",
        value: amountForCurrency(summary.income.cash_by_currency ?? {}, summary.income.cash, currency),
        color: METHOD_COLORS.cash,
      },
      {
        key: "cashless",
        label: "Безналичные",
        value: amountForCurrency(summary.income.cashless_by_currency ?? {}, summary.income.cashless, currency),
        color: METHOD_COLORS.kaspi,
      },
    ],
    counts: {},
    fallback: true,
  };
}
```

- [ ] **Step 4: Экран долгов** — создать `frontend/src/components/cashier/mobile/debts-screen.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ChevronRight, RefreshCw, Search } from "lucide-react";
import { ActionCard } from "@/components/ui/action-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { formatCompactCurrency, pluralRu } from "@/lib/utils";
import { debtPaymentState, matchesDebtQuery } from "../debt-state";
import type { CashierModel } from "../use-cashier";
import { useOverdueCheck } from "../use-overdue-check";

/** Долги клиентов списком: имя и телефон слева, остаток справа, тап — в карточку клиента. */
export function DebtsScreen({ model }: { model: CashierModel }) {
  const { debtRows: rows, debts, debtTotals, debtsReady, perms } = model;
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(25);
  const overdue = useOverdueCheck(debts.reload);
  const filtered = rows.filter((row) => matchesDebtQuery(row, q));
  const visible = filtered.slice(0, limit);
  const totalsLine = [
    formatCompactCurrency(debtTotals.total, debtTotals.currency),
    ...debtTotals.other.map(([unit, value]) => formatCompactCurrency(value, unit)),
  ].join(" + ");

  return (
    <section className="flex flex-col gap-4">
      {debtsReady && (
        <p className="text-[13px] text-[var(--muted-foreground)]">
          Дебиторка <span className="font-semibold text-[var(--foreground)]">{totalsLine}</span>
          {` · ${debtTotals.clients} ${pluralRu(debtTotals.clients, ["клиент", "клиента", "клиентов"])}`}
          {debtTotals.overdue > 0 ? ` · ${debtTotals.overdue} с просрочкой` : ""}
        </p>
      )}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту или телефону" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {perms.canCheckOverdue && (
          <Button
            variant="outline"
            size="icon"
            disabled={overdue.busy}
            onClick={() => void overdue.run()}
            aria-label="Проверить просрочки"
          >
            <RefreshCw className={"size-4" + (overdue.busy ? " animate-spin" : "")} />
          </Button>
        )}
      </div>
      {overdue.message && (
        <p className="rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {overdue.message}
        </p>
      )}
      {debts.error && rows.length === 0 && <ErrorAlert message={debts.error} onRetry={debts.reload} />}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
        {debts.loading ? (
          <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
        ) : !debts.error && filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {visible.map((row) => {
              const state = debtPaymentState(row);
              return (
                <li key={row.client_id}>
                  <ActionCard
                    primaryAction={{
                      kind: "link",
                      href: `/accounting/debts/clients/${row.client_id}`,
                      label: `Долг клиента ${row.client_name || row.client_id}`,
                    }}
                    className="flex items-center gap-3 px-4 py-3.5"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-semibold">{row.client_name || "—"}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--muted-foreground)]">
                        <span>
                          {row.orders_count} {pluralRu(row.orders_count, ["заказ", "заказа", "заказов"])}
                        </span>
                        <Badge tone={state.tone} dot>
                          {state.label}
                        </Badge>
                        {row.stores_count > 0 && (
                          <span>
                            {row.stores_count} {pluralRu(row.stores_count, ["магазин", "магазина", "магазинов"])}
                          </span>
                        )}
                        {row.overdue_count > 0 && (
                          <Badge tone="destructive" dot>
                            {row.overdue_count} {pluralRu(row.overdue_count, ["просрочка", "просрочки", "просрочек"])}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0 text-right text-[15px] font-semibold tabular-nums text-[var(--destructive)]">
                      <CurrencyAmounts
                        byCurrency={row.debt_by_currency}
                        fallbackAmount={row.debt_total}
                        fallbackCurrency={row.debt_currency ?? "KZT"}
                      />
                    </div>
                    <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
                  </ActionCard>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <LoadMore
        shown={visible.length}
        total={filtered.length}
        hasMore={filtered.length > visible.length}
        onClick={() => setLimit((current) => current + 25)}
      />
    </section>
  );
}
```

- [ ] **Step 5: Экран отчёта** — создать `frontend/src/components/cashier/mobile/report-screen.tsx`:

```tsx
"use client";
import { useState } from "react";
import { CalendarDays } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { ErrorAlert } from "@/components/ui/data-state";
import { DonutChart } from "@/components/ui/donut-chart";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Tabs } from "@/components/ui/tabs";
import { formatCurrency, formatIsoDate, pluralRu } from "@/lib/utils";
import { PERIOD_PRESETS, periodPresetOf, periodRange } from "../filters";
import type { CashierModel } from "../use-cashier";
import { reportSegments, type Breakdown } from "./report-segments";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[13px]">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

/** Отчёт по поступлениям: период чипами, кольцо с итогом, разрез по отделам или способам, легенда строками. */
export function ReportScreen({ model }: { model: CashierModel }) {
  const { income, incomeReady, summary, filtersByScreen, patchFilters } = model;
  const [breakdown, setBreakdown] = useState<Breakdown>("departments");
  const [customOpen, setCustomOpen] = useState(false);
  const filters = filtersByScreen.report;
  const preset = periodPresetOf(filters);
  const { segments, counts } = reportSegments(summary.data, breakdown, income.currency);
  const positiveTotal = segments.reduce((sum, segment) => sum + Math.max(segment.value, 0), 0);
  const money = (value: number, currency = income.currency) => formatCurrency(value, currency);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex gap-2 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {PERIOD_PRESETS.map((item) => (
          <Chip key={item.key} active={preset === item.key} onClick={() => patchFilters(periodRange(item.key))}>
            {item.label}
          </Chip>
        ))}
        <Chip active={preset === "custom"} onClick={() => setCustomOpen(true)}>
          <CalendarDays className="size-3.5" />
          {preset === "custom"
            ? `${filters.dateFrom ? formatIsoDate(filters.dateFrom) : "…"} – ${filters.dateTo ? formatIsoDate(filters.dateTo) : "…"}`
            : "Период…"}
        </Chip>
      </div>

      {summary.error && <ErrorAlert message={summary.error} onRetry={summary.reload} />}

      <Card>
        <CardContent className="flex flex-col items-stretch gap-4 p-5">
          <DonutChart
            segments={segments}
            centerValue={incomeReady ? money(income.total) : "—"}
            centerLabel={incomeReady ? `${income.payments} ${pluralRu(income.payments, ["оплата", "оплаты", "оплат"])}` : "Загрузка…"}
            emptyLabel="За период поступлений нет"
          />
          <Tabs
            variant="segment"
            label="Разрез отчёта"
            className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
            tabs={[
              { key: "departments", label: "По отделам" },
              { key: "methods", label: "По способу" },
            ]}
            active={breakdown}
            onChange={(key) => setBreakdown(key as Breakdown)}
          />
          {incomeReady && segments.length === 0 ? (
            <p className="text-center text-sm text-[var(--muted-foreground)]">За период поступлений нет.</p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {segments.map((segment) => {
                const share = positiveTotal > 0 && segment.value > 0 ? Math.round((segment.value / positiveTotal) * 100) : 0;
                const count = counts[segment.key];
                return (
                  <li key={segment.key} className="flex items-center gap-3 py-2.5">
                    <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: segment.color }} />
                    <span className="min-w-0 flex-1 truncate text-[15px]">{segment.label}</span>
                    <span className="text-right">
                      <span className="block text-[15px] font-semibold tabular-nums">{money(segment.value)}</span>
                      <span className="block text-xs text-[var(--muted-foreground)]">
                        {share}%{count !== undefined ? ` · ${count} ${pluralRu(count, ["оплата", "оплаты", "оплат"])}` : ""}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {incomeReady && (income.refunded > 0 || income.otherCurrencies.length > 0 || income.otherRefunds.length > 0) && (
        <Card>
          <CardContent className="flex flex-col gap-2 p-4">
            {income.refunded > 0 && (
              <>
                <Row label="Поступило до возвратов" value={money(income.gross)} />
                <Row label="Возвращено" value={money(income.refunded)} />
              </>
            )}
            {income.otherCurrencies.map(([currency, value]) => (
              <Row key={currency} label="Также чистыми" value={money(value, currency)} />
            ))}
            {income.otherRefunds.flatMap(([currency, value]) => [
              <Row key={`${currency}-gross`} label={`Поступило до возвратов, ${currency}`} value={money(income.grossFor(currency), currency)} />,
              <Row key={`${currency}-refund`} label={`Возвращено, ${currency}`} value={money(value, currency)} />,
            ])}
          </CardContent>
        </Card>
      )}

      <Modal
        variant="sheet"
        open={customOpen}
        onClose={() => setCustomOpen(false)}
        eyebrow="Отчёт"
        title="Свой период"
        footer={<Button onClick={() => setCustomOpen(false)}>Готово</Button>}
      >
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">С даты</span>
            <Input type="date" value={filters.dateFrom} onChange={(e) => patchFilters({ dateFrom: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
            <Input type="date" value={filters.dateTo} onChange={(e) => patchFilters({ dateTo: e.target.value })} />
          </label>
        </div>
      </Modal>
    </section>
  );
}
```

- [ ] **Step 6: Подключить экраны** — в `mobile-cashier.tsx` добавить импорты `import { DebtsScreen } from "./debts-screen"; import { ReportScreen } from "./report-screen";` и строки:

```tsx
      {view === "debts" && <DebtsScreen model={model} />}
      {view === "report" && <ReportScreen model={model} />}
```

- [ ] **Step 7: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier && npx tsc --noEmit`
Expected: PASS.

---

### Task 11: Экран «Транзакции» — лента по дням и шторка деталей с действиями

**Files:**
- Create: `frontend/src/components/cashier/mobile/transactions-screen.tsx`
- Modify: `mobile-cashier.tsx` (импорт + строка), `mobile-cashier.test.tsx` (сценарий)

**Interfaces:**
- Consumes: `useTransactions`, `TransactionModals`, `PaidMethodSummary` (Task 7), `Chip`, существующие `groupByDay` + `useLocalDay`, `FilterDropdown`, `DataGate`, `LoadMore`, `Badge`, `paymentStage`.
- Produces: `TransactionsScreen({ model })`.

- [ ] **Step 1: Падающий тест** — в `mobile-cashier.test.tsx` добавить:

```tsx
it("opens a transaction sheet with actions and hands off to the refund modal", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=transactions");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /PAY-000005/ }));
  const sheet = await screen.findByRole("dialog", { name: "Оплачено" });
  expect(within(sheet).getByText("Журнал операции")).toBeInTheDocument();
  await user.click(within(sheet).getByRole("button", { name: /Вернуть оплату/ }));
  expect(await screen.findByRole("dialog", { name: "Вернуть оплату" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Оплачено" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd frontend && npx vitest run src/components/cashier/mobile`
Expected: FAIL — на экране транзакций пусто.

- [ ] **Step 3: Экран** — создать `frontend/src/components/cashier/mobile/transactions-screen.tsx`:

```tsx
"use client";
import { ChevronRight, RefreshCcw, Search } from "lucide-react";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { paymentStage } from "@/lib/constants";
import { groupByDay } from "@/lib/day-groups";
import { useLocalDay } from "@/lib/use-local-day";
import { currencySymbol, formatMoney, formatTime } from "@/lib/utils";
import type { CashierModel } from "../use-cashier";

/** Транзакции на телефоне: сводка, поиск и чипы, лента по дням; тап по строке — шторка деталей с действиями. */
export function TransactionsScreen({ model }: { model: CashierModel }) {
  const { perms, departments, queue } = model;
  const t = useTransactions({ onChanged: queue.reload });
  const { data, rows, meta, loading, loadError } = t;
  const currentDay = useLocalDay();
  const groups = groupByDay(rows, (row) => new Date(row.paid_at), currentDay);

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardContent className="p-4">
          <div className="text-[13px] text-[var(--muted-foreground)]">Оплачено</div>
          <div className="mt-1 text-[22px] font-bold tabular-nums text-[var(--success)]">
            {formatMoney(data?.summary.paid_by_currency.KZT ?? 0)} ₸
            {Number(data?.summary.paid_by_currency.USD ?? 0) > 0 && (
              <span className="ml-2 text-base text-[var(--muted-foreground)]">
                + {formatMoney(data!.summary.paid_by_currency.USD)} $
              </span>
            )}
          </div>
          <PaidMethodSummary summary={data?.summary.paid_by_method} />
          <div className="mt-3 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3 text-[13px]">
            <div>
              <div className="text-[var(--muted-foreground)]">Возвращено</div>
              <div className="tabular-nums">
                {formatMoney(data?.summary.refunded_by_currency.KZT ?? 0)} ₸
                {Number(data?.summary.refunded_by_currency.USD ?? 0) > 0
                  ? ` + ${formatMoney(data!.summary.refunded_by_currency.USD)} $`
                  : ""}
              </div>
            </div>
            <div>
              <div className="text-[var(--muted-foreground)]">Операций</div>
              <div className="tabular-nums">{data?.count ?? 0}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {t.error && !t.refundFor && !t.rejectFor && !t.restoreFor && (
        <div className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2 text-sm text-[var(--destructive)]">
          {t.error}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            className="pl-9"
            placeholder="Клиент, заказ или операция"
            value={t.query}
            onChange={(e) => t.setQuery(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <FilterDropdown
            label="Отдел"
            active={t.department}
            onChange={t.setDepartment}
            options={[{ key: "all", label: "Все" }, ...departments.map((row) => ({ key: row.code, label: row.name }))]}
          />
          <Button variant="outline" size="icon" aria-label="Обновить" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" />
          </Button>
        </div>
        {/* Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */}
        <div className="flex gap-1.5 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {t.statusItems.map((item) => (
            <Chip key={item.key} active={t.statusFilter === item.key} onClick={() => t.setStatusFilter(item.key)}>
              {item.label}
              <span className="tabular-nums">{item.count}</span>
            </Chip>
          ))}
        </div>
      </div>

      {((loading && rows.length === 0) || loadError) && (
        <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={t.reload} />
      )}
      {!loading && !loadError && rows.length === 0 && (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
      )}

      {groups.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          <h2 className="px-1 text-[15px] font-semibold">{group.label}</h2>
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
            {group.items.map((row) => {
              const state = paymentStage(row.effective_status ?? row.status);
              const refunded = Number(row.refunded_amount ?? 0);
              const pendingRefund = Number(row.pending_refund_amount ?? 0);
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => t.openStatus(row)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-medium">
                        PAY-{String(row.id).padStart(6, "0")} · {row.client_name ?? "—"}
                      </div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {formatTime(row.paid_at)} · заказ #{row.order} · {row.method_label ?? row.method}
                        {row.provider?.channel === "qr" ? " · Kaspi QR" : ""}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1 text-right">
                      <span className="text-[15px] font-semibold tabular-nums">
                        {formatMoney(row.amount)} {currencySymbol(row.currency)}
                      </span>
                      <Badge tone={state.tone}>{state.label}</Badge>
                      {refunded > 0 ? (
                        <span className="text-xs tabular-nums text-[var(--muted-foreground)]">
                          возврат {formatMoney(refunded)} {currencySymbol(row.currency)}
                        </span>
                      ) : pendingRefund > 0 ? (
                        <span className="text-xs text-[var(--warning)]">{formatMoney(pendingRefund)} в обработке</span>
                      ) : null}
                    </div>
                    <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {rows.length > 0 && (
        <LoadMore
          shown={rows.length}
          total={meta?.count ?? rows.length}
          hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
          loading={loading && t.page > 1}
          onClick={t.loadNextPage}
        />
      )}

      <TransactionModals t={t} canConfirm={perms.canPayments} canCreate={perms.canCreatePayments} sheet detailActions />
    </section>
  );
}
```

- [ ] **Step 4: Подключить** — в `mobile-cashier.tsx` добавить `import { TransactionsScreen } from "./transactions-screen";` и строку:

```tsx
      {view === "transactions" && <TransactionsScreen model={model} />}
```

После этого переключатель в `MobileCashier` покрывает все шесть мобильных экранов; `overview` на телефон не попадает (`resolveView` сводит его к `home`).

- [ ] **Step 5: Тесты зелёные**

Run: `cd frontend && npx vitest run src/components/cashier src/components/transactions src/app/accounting && npx tsc --noEmit`
Expected: PASS.

---

### Task 12: Полная проверка — check, build, прокликивание на 375px

**Files:** без новых файлов; правки — только если проверка что-то вскроет.

- [ ] **Step 1: Бэкенд**

Run: `cd backend && .venv/bin/pytest apps/orders/tests/test_reports.py apps/orders/tests/test_department_payment_breakdown.py -q`
Expected: PASS.

- [ ] **Step 2: Фронт целиком**

Run: `cd frontend && npx prettier --write src && npm run check`
Expected: format:check, lint (`--max-warnings=0`), typecheck и все тесты — зелёные. Если prettier переформатировал файлы вне задачи — откатить их (`git checkout -- <файл>`), править только свои.

- [ ] **Step 3: Сборка**

Run: `cd frontend && npm run build`
Expected: сборка проходит; в выводе нет предупреждения про `useSearchParams` без Suspense для `/accounting`. После сборки перезапустить dev-сервер (общий `.next`).

- [ ] **Step 4: Стенд** — через Browser pane: `preview_start` `backend-local` (runserver 127.0.0.1:8000, локальная база `asyl`), затем `preview_start` `frontend-local-api` (API переопределён на 127.0.0.1:8000). Попросить пользователя войти самому (`admin_test` / пароль вводит он); пароли в браузер не печатать. `resize_window` preset `mobile` (375×812), перезагрузить страницу `/accounting`.

- [ ] **Step 5: Сценарии на телефоне** (скриншот после каждого, ошибки консоли — `read_console_messages`):
  1. Главная: карточка «Поступления за сегодня», пять пунктов меню с подзаголовками; тап по пункту → подэкран с «‹ Касса» в топбаре; «‹» возвращает на главную; системная «назад» браузера тоже.
  2. Заявки и оплаты: сегмент «Оплаты/Заявки», подтверждение одной оплаты (тост, строка исчезает), «Отклонить», открытие формы «Проверить и подтвердить» на весь экран и закрытие.
  3. Иконка фильтров → шторка снизу: отдел/магазин нативными селектами, «Сбросить», «Готово»; бейдж со счётчиком на иконке.
  4. Отчёт: чипы периода (по умолчанию «Сегодня»), кольцо и легенда, переключение «По отделам / По способу», «Период…» → шторка с датами.
  5. Долги: поиск, строка ведёт на карточку клиента, «Проверить просрочки» (если есть право).
  6. Журнал: группы по дням, «Вернуть на подтверждение».
  7. Транзакции: чипы статусов прокручиваются, тап по строке → шторка деталей → «Вернуть оплату» открывает модалку возврата (не отправлять — закрыть).
  8. Тёмная тема (переключатель в топбаре): кольцо, чипы, шторки читаемы.
  9. `resize_window` preset `desktop`: `/accounting` выглядит как до изменений; вкладки меняют `?view=` в адресе.

- [ ] **Step 6: Результат** — сообщить пользователю: что проверено, скриншоты ключевых экранов, что не коммитили (ждём «пушни»). Если стенд поднять не удалось (нет базы/учётки) — сказать прямо, что прокликивание не сделано, и почему.

---

## Self-review

**Покрытие спеки.** Границы (`useIsMobile`, `max-md`) — T2/T3; `?view=` с push/replace/back — T6/T8; таблица значений `view` — T5 (`resolveView`); права — T5; главная с карточкой, меню, подзаголовками и `page_size=1` — T8 + `use-cashier` (T6); отчёт (чипы, кольцо, разрез, легенда, возвраты, другие валюты, фолбэк) — T10; долги — T10; заявки и оплаты (сегмент, строки, действия, диалоги) — T9; журнал по дням с восстановлением — T9; транзакции (сводка, поиск, чипы, лента, шторка с действиями, модалки) — T7/T11; фильтры по экранам, пресеты, `CashFilterFields`/панель/шторка — T5/T6/T8; матрица загрузки и поллинг — T6; бэкенд `by_method` — T1; типы с фолбэком — T2/T10; тесты из спеки — T2, T5, T6 (старые), T8–T11 (мобильные), T1 (pytest); финальная проверка — T12. Вне скоупа детальные страницы долгов — не трогаются.

**Заглушки.** Нет «TBD/TODO/аналогично задаче N»; перенос кода задан номерами строк текущих файлов + полным кодом изменённых частей.

**Согласованность имён.** `CashierModel` (T6) потребляют T8–T11; `useCashierQueue` возвращает `queuePage/pendingPage/toReview/pendingOrders/refresh/reload/busy/error/loadError/confirmPayment/receivePayment/rejectPayment/reopenPayment/restorePayment/confirmOrder` — как в текущем `page.tsx`; `Transactions` (T7) даёт `openStatus/closeStatus/loadNextPage/statusItems/setQuery/setDepartment/setStatusFilter/page` — использованы в T7 и T11; `TransactionActionHandlers` — T7 тест и `TransactionModals`; `activeFilterCount(filters, { dates, remaining })` — T5/T6/T8; `periodRange/periodPresetOf/PERIOD_PRESETS` — T5/T10; `groupByDay(items, dateOf, currentDay)` + `useLocalDay` (существующие) — T9/T11; `TopbarBack` — T3/T8 (мок AppShell).
