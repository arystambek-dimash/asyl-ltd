import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import type { ShipmentSourcesSheet } from "@/components/loader/shipment-sources-sheet";
import type { DispatchSource, DispatchSources, HistoryReportScope, LoaderOrder, SourceAnswers } from "@/lib/loader";
import { pagedState } from "@/test-utils/api";
import { makeLoaderItem, makeLoaderOrder } from "@/test-utils/factories";
import { formatTime, shiftIsoDate, todayLocalIsoDate } from "@/lib/utils";
import type { WagonReportSent } from "@/lib/wagon-report";

import LoaderPage from "./page";

function blobError(detail: string): AxiosError {
  const error = new AxiosError("failed");
  error.response = {
    status: 400,
    statusText: "",
    data: new Blob([JSON.stringify({ detail })], { type: "application/json" }),
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

/** Отказ API с машинным кодом; текст — то, что покажет apiError (в этом файле — message). */
function codedError(code: string, detail: string): AxiosError {
  const error = new AxiosError(detail);
  error.response = {
    status: 400,
    statusText: "",
    data: { code, detail },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

const ALL_AREAS = ["loader.view", "loader.confirm", "loader.trucks", "loader.wagons"];

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  paged: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  openWaybill: vi.fn(),
  reload: vi.fn(),
  refresh: vi.fn(),
  applyItems: vi.fn(),
  polling: [] as { poll: () => Promise<unknown>; interval: number; active: boolean }[],
  apiUrls: [] as (string | null)[],
  railRow: null as LoaderOrder | null,
  reportSent: null as WagonReportSent | null,
  // Что «грузчик» ответит в заглушке опросника и что она отдаст в отгрузку.
  sourceAnswers: {} as SourceAnswers,
  sources: [] as DispatchSource[],
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.paged }));
// Счётчики (просроченные, соседняя вкладка) — отдельные лёгкие запросы.
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.apiUrls.push(url);
    return {
      data: url?.includes("overdue=1") ? { count: 3 } : url?.includes("page_size=1") ? { count: 4 } : null,
      error: "",
      loading: false,
      reload: vi.fn(),
    };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, interval: number, active = true) => {
    mocks.polling.push({ poll, interval, active });
  },
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  // blobApiError и apiErrorCode настоящие: накладная приходит Blob-ом, код отказа — в теле ошибки.
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (e: Error) => e.message,
}));
vi.mock("@/lib/loader", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/loader")>()),
  openWaybill: mocks.openWaybill,
}));
// Лист отчёта проверен своими тестами; здесь — как страница его открывает и применяет ответ.
vi.mock("@/components/loader/rail-report-sheet", () => ({
  RailReportSheet: ({ orderId, onApplied }: { orderId: number; onApplied: (row: LoaderOrder) => void }) => (
    <div role="dialog" aria-label="Отчёт о вагонах">
      <span>{`заказ ${orderId}`}</span>
      <button type="button" onClick={() => onApplied(mocks.railRow!)}>
        Провести отчёт
      </button>
    </div>
  ),
}));
// Окно отправки проверено своими тестами; здесь — с чем страница его открывает и как применяет ответ.
vi.mock("@/components/loader/wagon-report-modal", () => ({
  WagonReportModal: ({ scope, onSent }: { scope: HistoryReportScope; onSent: (sent: WagonReportSent) => void }) => (
    <div role="dialog" aria-label="Отправить отчёт">
      <span>{JSON.stringify(scope)}</span>
      <button type="button" onClick={() => onSent(mocks.reportSent!)}>
        Отправить
      </button>
    </div>
  ),
}));
// Кнопка копирования проверена своими тестами; здесь — где она стоит и какой отчёт копирует.
vi.mock("@/components/loader/copy-truck-report-button", () => ({
  CopyTruckReportButton: ({
    scope,
    label = "Скопировать отчёт",
    disabled,
  }: {
    scope: HistoryReportScope;
    label?: string;
    disabled?: boolean;
  }) => (
    <button type="button" data-scope={JSON.stringify(scope)} disabled={disabled}>
      {label}
    </button>
  ),
}));
// Окно настроек накладной — со своим черновиком; здесь важно, что каждое открытие начинается заново.
vi.mock("@/components/loader/waybill-settings-modal", async () => {
  const { useState } = await import("react");
  return {
    WaybillSettingsModal: function WaybillSettingsStub({ onClose }: { onClose: () => void }) {
      const [draft, setDraft] = useState("");
      return (
        <div role="dialog" aria-label="Накладная">
          <input aria-label="Точка в шапке" value={draft} onChange={(event) => setDraft(event.target.value)} />
          <button type="button" onClick={onClose}>
            Отмена
          </button>
        </div>
      );
    },
  };
});
// Опросник проверен своими тестами; здесь — с чем страница его открывает и что делает с ответом.
// Как настоящий лист, заглушка выбирает первый шаг при монтировании: полные ответы — сразу сводка.
vi.mock("@/components/loader/shipment-sources-sheet", async () => {
  const { useState } = await import("react");
  const { answersComplete } = await import("@/lib/loader");
  return {
    ShipmentSourcesSheet: function ShipmentSourcesStub({
      context,
      answers,
      onAnswers,
      busy,
      error,
      notice,
      onClose,
      onConfirm,
    }: ComponentProps<typeof ShipmentSourcesSheet>) {
      const [start] = useState(() => (answersComplete(context, answers) ? "сводка" : "первый товар"));
      return (
        <div role="dialog" aria-label="С какого склада?">
          <span>{`начало: ${start}`}</span>
          <span>{`товары: ${context.products.map((product) => `${product.label} — ${product.bags}`).join(", ")}`}</span>
          <span>{`ответы: ${JSON.stringify(answers)}`}</span>
          {notice && <p>{notice}</p>}
          {error && <p>{error}</p>}
          <button type="button" onClick={() => onAnswers(mocks.sourceAnswers)}>
            Ответить
          </button>
          <button type="button" disabled={busy} onClick={() => onConfirm(mocks.sources)}>
            Отгрузить
          </button>
          <button type="button" onClick={onClose}>
            Закрыть
          </button>
        </div>
      );
    },
  };
});
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));

/** Заказ ИП Мурат: два мешка «Д1с · Красный 50 кг» на 20 000 ₸. */
const order = (id: number, fields: Partial<LoaderOrder> = {}): LoaderOrder =>
  makeLoaderOrder(id, {
    client_name: "ИП Мурат",
    items: [makeLoaderItem({ quantity: 2, unit_price: "10000.00" })],
    bags: 2,
    total_kg: "100.00",
    total_amount: "20000.00",
    remaining_amount: "20000.00",
    ...fields,
  });

function paged(
  items: LoaderOrder[],
  fields: { refreshError?: string; applyItems?: typeof mocks.applyItems; hasMore?: boolean } = {},
) {
  return pagedState(items, { reload: mocks.reload, refresh: mocks.refresh, applyItems: mocks.applyItems, ...fields });
}

const queueUrls = () =>
  mocks.paged.mock.calls.map(([url]) => url).filter((url) => typeof url === "string" && url.includes("queue"));
const lastPoll = () => mocks.polling.at(-1)!;

/** Склад один — выбирать не из чего: лист не открывается. */
const ONE_WAREHOUSE: DispatchSources = {
  choose: false,
  warehouses: [{ id: 1, name: "Мельница" }],
  products: [{ product: 12, label: "Д1с · Красный 50 кг", color: "Red", bags: 2, short: {} }],
};
/** Два склада; на «Мельнице 2» карточки нет — там нехватка. */
const SPLIT: DispatchSources = {
  choose: true,
  warehouses: [
    { id: 1, name: "Мельница" },
    { id: 2, name: "Мельница 2" },
  ],
  products: [{ product: 12, label: "Д1с · Красный 50 кг", color: "Red", bags: 2, short: { "2": 0 } }],
};
/** Ответ грузчика: мешок с «Мельницы», мешок с «Мельницы 2». */
const SPLIT_ANSWERS: SourceAnswers = { 12: { 1: 1, 2: 1 } };
const SPLIT_SOURCES: DispatchSource[] = [
  { product: 12, warehouse: 1, bags: 1 },
  { product: 12, warehouse: 2, bags: 1 },
];
const SOURCES_CHANGED = "Состав заказа или склады изменились — ответьте заново";
const SHEET = { name: "С какого склада?" };

/** Открыть заказ 624 из очереди фур, ввести тягач и нажать «Подтвердить отгрузку». */
async function confirmTruck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /№624/ }));
  await user.type(screen.getByLabelText("Тягач"), "403 bjn 13");
  await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
}

describe("LoaderPage", () => {
  beforeEach(() => {
    mocks.permissions = ALL_AREAS;
    mocks.post.mockReset();
    // По умолчанию склад один: прежние проверки отгрузки фуры держатся без опросника.
    mocks.get.mockReset().mockResolvedValue({ data: ONE_WAREHOUSE });
    mocks.sourceAnswers = SPLIT_ANSWERS;
    mocks.sources = SPLIT_SOURCES;
    mocks.openWaybill.mockReset().mockResolvedValue(undefined);
    mocks.reload.mockReset();
    mocks.refresh.mockReset().mockResolvedValue(undefined);
    mocks.applyItems.mockReset();
    mocks.polling = [];
    mocks.apiUrls = [];
    mocks.railRow = null;
    mocks.reportSent = null;
    localStorage.clear();
    mocks.paged.mockReset().mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/?transport=train"))
        return paged([
          order(625, {
            transport_type: "train",
            client_name: "ТОО Вагон",
            truck_number: "",
            bags: 1360,
            total_kg: "68000.00",
          }),
        ]);
      if (url?.startsWith("/loader/queue/")) return paged([order(624)]);
      if (url?.startsWith("/loader/history/"))
        return paged([
          order(620, { status: "shipped", shipped_at: "2026-09-16T11:31:00+05:00", truck_number: "612 BEX 13" }),
        ]);
      return paged([]);
    });
  });

  it("открывает заказ, подтверждает отгрузку кнопкой и печатает накладную", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", truck_number: "403 BJN 13" }) });
    render(<LoaderPage />);

    // В списке кнопки подтверждения нет — сначала открывается сам заказ.
    expect(screen.queryByRole("button", { name: /Подтвердить отгрузку/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^ИП Мурат.*№624/ }));

    // Номер всегда вводит оператор: пустое поле не даёт отгрузить.
    expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeDisabled();
    await user.type(screen.getByLabelText("Тягач"), "403 bjn 13");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    // Номер уходит слитно: пробелы и регистр — только запись. Нетронутый прицеп не отправляется.
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403BJN13" }),
    );
    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Печать накладной/ }));
    expect(mocks.openWaybill).toHaveBeenCalledWith(624);

    await user.click(screen.getByRole("button", { name: "К списку заказов" }));
    expect(screen.queryByText("Отгрузка подтверждена")).not.toBeInTheDocument();
  });

  it("без права подтверждения показывает очередь и заказ без кнопки отгрузки", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["loader.view", "loader.trucks"];
    render(<LoaderPage />);

    expect(screen.getByText("ИП Мурат")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^ИП Мурат.*№624/ }));
    expect(screen.queryByRole("button", { name: /Подтвердить отгрузку/ })).not.toBeInTheDocument();
  });

  it("подставляет номер из заказа и даёт оператору его исправить", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/") ? paged([order(624, { truck_number: "111 AAA 01" })]) : paged([]),
    );
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped" }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    const field = screen.getByLabelText("Тягач");
    expect(field).toHaveValue("111 AAA 01");
    await user.clear(field);
    await user.type(field, "403 bjn 13");
    await user.type(screen.getByLabelText("Прицеп (необязательно)"), "07kg837pb");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", {
      truck_number: "403BJN13",
      trailer_number: "07KG837PB",
    });
  });

  it("стирает ошибочный прицеп, а неисправленный номер не отправляет", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/")
        ? paged([order(624, { truck_number: "403BJN13", trailer_number: "07KG837PB" })])
        : paged([]),
    );
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped" }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.clear(screen.getByLabelText("Прицеп (необязательно)"));
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    // Пустой прицеп — «стереть»; тягач не правили — сервер его не трогает.
    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { trailer_number: "" });
  });

  it("у вагона уходит только его номер, склады не спрашиваются", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: order(625, { status: "shipped", transport_type: "train" }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    await user.click(screen.getByRole("button", { name: /№625/ }));
    await user.type(screen.getByLabelText("Номер вагона"), "0012 3456");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/625/dispatch/", { truck_number: "00123456" });
    // Вагоны — без опросника: всё со «Склада отгрузки».
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("подставляет прошлую пару клиента одним нажатием", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/")
        ? paged([
            order(624, {
              client_country: "Кыргызстан",
              transport_suggestions: [{ truck_number: "07KG695ADT", trailer_number: "07KG837PB" }],
            }),
          ])
        : paged([]),
    );
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped" }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    // Пустое поле подсказывает маску страны клиента.
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
    await user.click(screen.getByRole("button", { name: "07 KG 695 ADT / 07 KG 837 PB" }));

    expect(screen.getByLabelText("Тягач")).toHaveValue("07 KG 695 ADT");
    expect(screen.getByLabelText("Прицеп (необязательно)")).toHaveValue("07 KG 837 PB");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", {
      truck_number: "07KG695ADT",
      trailer_number: "07KG837PB",
    });
  });

  it("пару, указанную клиентом, показывает только для чтения", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/")
        ? paged([
            order(624, {
              truck_number: "403BJN13",
              transport_locked: true,
              transport_suggestions: [{ truck_number: "07KG695ADT", trailer_number: "" }],
            }),
          ])
        : paged([]),
    );
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));

    expect(screen.getByLabelText("Тягач")).toBeDisabled();
    expect(screen.getByLabelText("Прицеп (необязательно)")).toBeDisabled();
    expect(screen.getByText(/Номер указал клиент/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "07 KG 695 ADT" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeEnabled();
  });

  it("отменяет ошибочную отгрузку сразу после неё и из истории, применяя ответ", async () => {
    const user = userEvent.setup();
    const today = todayLocalIsoDate();
    const queueApply = vi.fn();
    const historyApply = vi.fn();
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/"))
        return paged([order(624, { truck_number: "111 AAA 01", planned_on: today })], { applyItems: queueApply });
      if (url?.startsWith("/loader/history/"))
        return paged([order(620, { status: "shipped", shipped_at: "2026-09-18T11:31:00+05:00", can_rollback: true })], {
          applyItems: historyApply,
        });
      return paged([]);
    });
    mocks.post.mockImplementation(async (url: string) => {
      if (url === "/loader/orders/624/dispatch/")
        return { data: order(624, { status: "shipped", can_rollback: true, planned_on: today }) };
      if (url === "/loader/orders/624/rollback/") return { data: order(624, { planned_on: today }) };
      // Заказ 620 ждали на прошлой неделе: под «Сегодня» он в очередь не встаёт.
      return { data: order(620, { planned_on: "2000-01-01" }) };
    });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
    await user.click(await screen.findByRole("button", { name: /Отменить отгрузку/ }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/rollback/", {}));
    expect(await screen.findByText(/Отгрузка заказа №624 отменена/)).toBeInTheDocument();
    // Ответ отмены — строка очереди: заказ встаёт на своё место без перезагрузки.
    const [putBack] = queueApply.mock.calls.at(-1)!;
    expect(putBack([order(626, { planned_on: today })]).map((row: LoaderOrder) => row.id)).toEqual([624, 626]);

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: /Отменить$/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/620/rollback/", {}));
    const [fromHistory] = historyApply.mock.calls.at(-1)!;
    expect(fromHistory([order(620), order(621)]).map((row: LoaderOrder) => row.id)).toEqual([621]);
    const [notShown] = queueApply.mock.calls.at(-1)!;
    expect(notShown([order(626, { planned_on: today })]).map((row: LoaderOrder) => row.id)).toEqual([626]);
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("под поиском возвращённый заказ сверяет с сервером тихое обновление", async () => {
    const user = userEvent.setup();
    const queueApply = vi.fn();
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/")) return paged([], { applyItems: queueApply });
      if (url?.startsWith("/loader/history/"))
        return paged([order(620, { status: "shipped", shipped_at: "2026-09-18T11:31:00+05:00", can_rollback: true })]);
      return paged([]);
    });
    mocks.post.mockResolvedValue({ data: order(620, { planned_on: todayLocalIsoDate() }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.type(screen.getByLabelText("Поиск"), "Мурат");
    await waitFor(() => expect(mocks.paged).toHaveBeenLastCalledWith(expect.stringContaining("search=")));
    await user.click(screen.getByRole("button", { name: /Отменить$/ }));

    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    // Правило поиска знает только сервер: сами строку не вставляем.
    const [update] = queueApply.mock.calls.at(-1)!;
    expect(update([]).map((row: LoaderOrder) => row.id)).toEqual([]);
  });

  it("показывает оплату заказа, но отгрузить даёт и в долг", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/")
        ? paged([
            order(700, {
              truck_number: "111 AAA 01",
              payment_status: "settled",
              remaining_amount: "0.00",
            }),
            order(701, {
              truck_number: "222 BBB 02",
              payment_status: "unpaid",
              remaining_amount: "20000.00",
            }),
            // Заказ без суммы: остатка нет, но бэкенд считает его не оплаченным.
            order(702, {
              truck_number: "333 CCC 03",
              payment_status: "unpaid",
              remaining_amount: "0.00",
            }),
          ])
        : paged([]),
    );
    render(<LoaderPage />);

    expect(screen.getByText("Оплачен")).toBeInTheDocument();
    expect(screen.getByText(/Не оплачен · 20 000 ₸/)).toBeInTheDocument();
    expect(screen.getByText(/Не оплачен · 0 ₸/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /№701/ }));
    expect(screen.getByText(/Не оплачен · 20 000 ₸/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeEnabled();
  });

  it("открывается на сегодняшнем дне, просроченные — отдельной кнопкой", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/") ? paged([order(624)]) : paged([]),
    );
    render(<LoaderPage />);

    // Очередь спрашивается за сегодня вместе с просрочкой за 3 дня; параллельный вызов истории с null не мешает.
    const lastQueueUrl = () => queueUrls().at(-1);
    const today = todayLocalIsoDate();
    expect(lastQueueUrl()).toBe(`/loader/queue/?transport=truck&day=${today}&overdue_from=${shiftIsoDate(today, -3)}`);
    expect(screen.getByRole("button", { name: "Сегодня" })).toHaveAttribute("aria-pressed", "true");

    // Весь хвост — по-прежнему отдельной кнопкой, без окна.
    await user.click(await screen.findByRole("button", { name: /Просрочено · 3/ }));
    expect(lastQueueUrl()).toBe("/loader/queue/?transport=truck&overdue=1");

    await user.click(screen.getByRole("button", { name: "Все" }));
    expect(lastQueueUrl()).toBe("/loader/queue/?transport=truck");

    await user.click(screen.getByRole("button", { name: "Завтра" }));
    expect(lastQueueUrl()).toBe(`/loader/queue/?transport=truck&day=${shiftIsoDate(today, 1)}`);
    expect(screen.queryByLabelText("Просроченные за")).not.toBeInTheDocument();
  });

  describe("просрочка за последние дни под «Сегодня»", () => {
    const today = todayLocalIsoDate();
    const yesterday = shiftIsoDate(today, -1);
    const withQueue = (rows: LoaderOrder[]) =>
      mocks.paged.mockImplementation((url: string | null) =>
        url?.startsWith("/loader/queue/") ? paged(rows) : paged([]),
      );
    const before = (first: HTMLElement, second: HTMLElement) =>
      Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

    it("ниже сегодняшних — блок «Просрочено» с окном в 3 дня", () => {
      withQueue([order(701, { planned_on: today }), order(702, { planned_on: yesterday })]);
      render(<LoaderPage />);

      const heading = screen.getByRole("heading", { name: "Просрочено" });
      expect(screen.getByLabelText("Просроченные за")).toHaveValue("3");
      expect(before(screen.getByRole("button", { name: /№701/ }), heading)).toBe(true);
      expect(before(heading, screen.getByRole("button", { name: /№702/ }))).toBe(true);
      expect(screen.queryByText(/Просроченных за/)).not.toBeInTheDocument();
    });

    it("окно меняется, запоминается и у каждой вкладки своё", async () => {
      const user = userEvent.setup();
      withQueue([order(702, { planned_on: yesterday })]);
      const { unmount } = render(<LoaderPage />);

      await user.selectOptions(screen.getByLabelText("Просроченные за"), "7");
      expect(queueUrls().at(-1)).toBe(
        `/loader/queue/?transport=truck&day=${today}&overdue_from=${shiftIsoDate(today, -7)}`,
      );
      expect(localStorage.getItem("loader:overdue-days:truck:1")).toBe("7");

      // Вагоны — со своим окном: по умолчанию 3 дня; счётчик вкладки — тем же окном.
      expect(mocks.apiUrls).toContainEqual(
        `/loader/queue/?transport=train&day=${today}&overdue_from=${shiftIsoDate(today, -3)}&page=1&page_size=1`,
      );
      await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
      expect(queueUrls().at(-1)).toBe(
        `/loader/queue/?transport=train&day=${today}&overdue_from=${shiftIsoDate(today, -3)}`,
      );
      expect(screen.getByLabelText("Просроченные за")).toHaveValue("3");

      unmount();
      localStorage.setItem("loader:transport:1", "truck");
      render(<LoaderPage />);
      expect(queueUrls().at(-1)).toBe(
        `/loader/queue/?transport=truck&day=${today}&overdue_from=${shiftIsoDate(today, -7)}`,
      );
      expect(screen.getByLabelText("Просроченные за")).toHaveValue("7");
    });

    it("на сегодня пусто, а просрочка есть — не «Все заказы отгружены», а блок просрочки", () => {
      withQueue([order(702, { planned_on: yesterday })]);
      render(<LoaderPage />);

      expect(screen.queryByText("Все заказы отгружены")).not.toBeInTheDocument();
      expect(screen.queryByText("Ничего не найдено")).not.toBeInTheDocument();
      expect(screen.getByText("На сегодня ничего нет")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Просрочено" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /№702/ })).toBeInTheDocument();
    });

    it("просрочки за окно нет — тихая строка с выбором окна, чтобы расширить", async () => {
      const user = userEvent.setup();
      withQueue([order(701, { planned_on: today })]);
      render(<LoaderPage />);

      expect(screen.getByText("Просроченных за 3 дн. нет")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Просрочено" })).not.toBeInTheDocument();
      await user.selectOptions(screen.getByLabelText("Просроченные за"), "14");
      expect(queueUrls().at(-1)).toBe(
        `/loader/queue/?transport=truck&day=${today}&overdue_from=${shiftIsoDate(today, -14)}`,
      );
      expect(screen.getByText("Просроченных за 14 дн. нет")).toBeInTheDocument();
    });

    it("пусто и сегодня, и за окно — пустая очередь и та же тихая строка", () => {
      withQueue([]);
      render(<LoaderPage />);

      expect(screen.queryByText("Все заказы отгружены")).not.toBeInTheDocument();
      expect(screen.getByText("Просроченных за 3 дн. нет")).toBeInTheDocument();
      expect(screen.getByLabelText("Просроченные за")).toHaveValue("3");
    });

    it("следующая страница ещё не загружена — «просрочки нет» не утверждает", () => {
      mocks.paged.mockImplementation((url: string | null) =>
        url?.startsWith("/loader/queue/") ? paged([order(701, { planned_on: today })], { hasMore: true }) : paged([]),
      );
      render(<LoaderPage />);

      expect(screen.queryByText(/Просроченных за/)).not.toBeInTheDocument();
    });

    it("у вагонов — так же", async () => {
      const user = userEvent.setup();
      withQueue([
        order(801, { transport_type: "train", planned_on: today }),
        order(802, { transport_type: "train", planned_on: yesterday }),
      ]);
      render(<LoaderPage />);
      await user.click(screen.getByRole("tab", { name: /Вагоны/ }));

      const heading = screen.getByRole("heading", { name: "Просрочено" });
      expect(before(screen.getByRole("button", { name: /№801/ }), heading)).toBe(true);
      expect(before(heading, screen.getByRole("button", { name: /№802/ }))).toBe(true);
      await user.selectOptions(screen.getByLabelText("Просроченные за"), "1");
      expect(queueUrls().at(-1)).toBe(
        `/loader/queue/?transport=train&day=${today}&overdue_from=${shiftIsoDate(today, -1)}`,
      );
      expect(localStorage.getItem("loader:overdue-days:train:1")).toBe("1");
    });

    it("только под «Сегодня»: в «Просрочено», «Все» и выбранном дне блока и окна нет", async () => {
      const user = userEvent.setup();
      withQueue([order(701, { planned_on: today }), order(702, { planned_on: yesterday })]);
      render(<LoaderPage />);
      expect(screen.getByRole("heading", { name: "Просрочено" })).toBeInTheDocument();
      const noRecentOverdue = () => {
        expect(screen.queryByRole("heading", { name: "Просрочено" })).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Просроченные за")).not.toBeInTheDocument();
        expect(screen.queryByText(/Просроченных за/)).not.toBeInTheDocument();
      };

      await user.click(screen.getByRole("button", { name: /Просрочено · / }));
      noRecentOverdue();

      await user.click(screen.getByRole("button", { name: "Все" }));
      noRecentOverdue();

      fireEvent.change(screen.getByLabelText("Плановый день"), { target: { value: yesterday } });
      expect(queueUrls().at(-1)).toBe(`/loader/queue/?transport=truck&day=${yesterday}`);
      noRecentOverdue();
    });

    it("планшет простоял за полночь: «Сегодня» и «Завтра» — от нового дня, выбранный день остаётся", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      try {
        vi.setSystemTime(new Date(2026, 9, 2, 23, 59));
        const user = userEvent.setup();
        withQueue([]);
        const { rerender } = render(<LoaderPage />);
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-10-02&overdue_from=2026-09-29");

        // День и окно просрочки считаются от одного и того же «сегодня».
        vi.setSystemTime(new Date(2026, 9, 3, 0, 1));
        rerender(<LoaderPage />);
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-10-03&overdue_from=2026-09-30");
        expect(screen.getByLabelText("Плановый день")).toHaveValue("2026-10-03");

        await user.click(screen.getByRole("button", { name: "Завтра" }));
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-10-04");
        vi.setSystemTime(new Date(2026, 9, 4, 0, 1));
        rerender(<LoaderPage />);
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-10-05");

        // Выбранный вручную день — это дата, а не «сегодня»: полночь его не двигает.
        fireEvent.change(screen.getByLabelText("Плановый день"), { target: { value: "2026-09-25" } });
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-09-25");
        vi.setSystemTime(new Date(2026, 9, 5, 0, 1));
        rerender(<LoaderPage />);
        expect(queueUrls().at(-1)).toBe("/loader/queue/?transport=truck&day=2026-09-25");
        expect(screen.getByLabelText("Плановый день")).toHaveValue("2026-09-25");
      } finally {
        vi.useRealTimers();
      }
    });

    it("отменённая отгрузка встаёт в блок по порядку сервера: сегодня, затем вчера и старше", async () => {
      const user = userEvent.setup();
      const queueApply = vi.fn();
      mocks.paged.mockImplementation((url: string | null) => {
        if (url?.startsWith("/loader/queue/"))
          return paged([order(624, { planned_on: today })], { applyItems: queueApply });
        if (url?.startsWith("/loader/history/"))
          return paged([order(620, { status: "shipped", shipped_at: `${today}T11:31:00+05:00`, can_rollback: true })]);
        return paged([]);
      });
      mocks.post.mockResolvedValue({ data: order(620, { planned_on: yesterday }) });
      render(<LoaderPage />);

      await user.click(screen.getByRole("tab", { name: /История/ }));
      await user.click(screen.getByRole("button", { name: /Отменить$/ }));

      await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/620/rollback/", {}));
      const [putBack] = queueApply.mock.calls.at(-1)!;
      const rows = [order(624, { planned_on: today }), order(610, { planned_on: shiftIsoDate(today, -2) })];
      expect(putBack(rows).map((row: LoaderOrder) => row.id)).toEqual([624, 620, 610]);
    });
  });

  it("группирует очередь по дням: просрочка отдельно от сегодняшних", async () => {
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/"))
        return paged([order(700, { planned_on: "2000-01-01" }), order(701, { planned_on: todayLocalIsoDate() })]);
      return paged([]);
    });
    render(<LoaderPage />);

    expect(screen.getByText("ПРОСРОЧЕНО")).toBeInTheDocument();
    expect(screen.getByText("СЕГОДНЯ")).toBeInTheDocument();
  });

  it("история за период с печатью накладной", async () => {
    const user = userEvent.setup();
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: "Вчера" }));

    expect(mocks.paged).toHaveBeenLastCalledWith(
      expect.stringMatching(/^\/loader\/history\/\?transport=truck&date_from=\d{4}-\d{2}-\d{2}&date_to=/),
    );
    await user.click(screen.getByRole("button", { name: /Накладная/ }));
    expect(mocks.openWaybill).toHaveBeenCalledWith(620);
  });

  it("накладная не открылась — показывает причину сервера, а не общую ошибку", async () => {
    mocks.openWaybill.mockRejectedValue(blobError("Накладная печатается после отгрузки"));
    const user = userEvent.setup();
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: /Накладная/ }));

    expect(await screen.findByText("Накладная печатается после отгрузки")).toBeInTheDocument();
  });

  it("вкладки «Фуры | Вагоны» по областям: запросы с транспортом, выбор запоминается", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<LoaderPage />);

    expect(screen.getByRole("tab", { name: /Фуры/ })).toHaveAttribute("aria-selected", "true");
    // Счётчик соседней вкладки — тем же фильтром очереди, одним лёгким запросом.
    expect(screen.getByRole("tab", { name: "Вагоны, 4" })).toBeInTheDocument();
    expect(mocks.apiUrls.filter(Boolean).every((url) => url!.includes("transport="))).toBe(true);
    expect(mocks.apiUrls).toContainEqual(
      expect.stringMatching(/^\/loader\/queue\/\?transport=train&day=.*page_size=1/),
    );

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    expect(queueUrls().at(-1)).toMatch(/^\/loader\/queue\/\?transport=train&day=/);
    // Карточка вагона: клиент, итог в мешках и тоннах.
    expect(screen.getByText("ТОО Вагон")).toBeInTheDocument();
    expect(screen.getByText("Итого 1360 мешков · 68 т")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /История/ }));
    expect(mocks.paged).toHaveBeenLastCalledWith(expect.stringMatching(/^\/loader\/history\/\?transport=train&/));

    unmount();
    render(<LoaderPage />);
    expect(screen.getByRole("tab", { name: /Вагоны/ })).toHaveAttribute("aria-selected", "true");
  });

  it("одна область — без переключателя", () => {
    mocks.permissions = ["loader.view", "loader.confirm", "loader.wagons"];
    localStorage.setItem("loader:transport:1", "truck");
    render(<LoaderPage />);

    expect(screen.queryByRole("tab", { name: /Фуры/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Вагоны/ })).not.toBeInTheDocument();
    expect(queueUrls().at(-1)).toMatch(/^\/loader\/queue\/\?transport=train&/);
    expect(screen.getByText("ТОО Вагон")).toBeInTheDocument();
  });

  it("без области ничего не запрашивает и объясняет почему", () => {
    mocks.permissions = ["loader.view", "loader.confirm"];
    render(<LoaderPage />);

    expect(screen.getByText(/Не выдана ни одна область/)).toBeInTheDocument();
    expect(queueUrls()).toEqual([]);
    expect(mocks.apiUrls.filter(Boolean)).toEqual([]);
  });

  it("тихо опрашивает очередь раз в 15 секунд и держит список при ошибке опроса", async () => {
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/") ? paged([order(624)], { refreshError: "Нет связи" }) : paged([]),
    );
    render(<LoaderPage />);

    expect(lastPoll().interval).toBe(15_000);
    expect(lastPoll().active).toBe(true);
    await lastPoll().poll();
    expect(mocks.refresh).toHaveBeenCalled();
    expect(mocks.reload).not.toHaveBeenCalled();
    // Ошибка опроса — маленькая пометка, последние данные остаются.
    expect(screen.getByText("ИП Мурат")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Не удалось обновить/);
  });

  it("не опрашивает, пока идёт отгрузка, и применяет ответ вместо перезагрузки", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    mocks.post.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.type(screen.getByLabelText("Тягач"), "403 bjn 13");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
    expect(lastPoll().active).toBe(false);

    finish({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    expect(mocks.applyItems).toHaveBeenCalled();
    const [update] = mocks.applyItems.mock.calls.at(-1)!;
    expect(update([order(624), order(626)]).map((row: LoaderOrder) => row.id)).toEqual([626]);
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("не опрашивает, пока открыты настройки накладной", async () => {
    const user = userEvent.setup();
    mocks.permissions = [...ALL_AREAS, "sys_permissions.manage"];
    render(<LoaderPage />);

    expect(lastPoll().active).toBe(true);
    await user.click(screen.getByRole("button", { name: "Настройки накладной" }));
    expect(lastPoll().active).toBe(false);
  });

  it("настройки накладной при повторном открытии не показывают отменённый черновик", async () => {
    const user = userEvent.setup();
    mocks.permissions = [...ALL_AREAS, "sys_permissions.manage"];
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: "Настройки накладной" }));
    await user.type(screen.getByLabelText("Точка в шапке"), "отменённая правка");
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog", { name: "Накладная" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Настройки накладной" }));
    expect(screen.getByLabelText("Точка в шапке")).toHaveValue("");
  });

  it("свою отгрузку не выдаёт за чужую, даже если опрос вернулся раньше её ответа", async () => {
    const user = userEvent.setup();
    let rows = [order(624), order(626)];
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/") ? paged(rows) : paged([]),
    );
    let finish!: (value: unknown) => void;
    mocks.post.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { rerender } = render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.type(screen.getByLabelText("Тягач"), "403 bjn 13");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    // Опрос, ушедший до нажатия, вернулся уже без заказа — ответ отгрузки ещё в пути.
    rows = [order(626)];
    rerender(<LoaderPage />);
    expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
    expect(screen.queryByText(/уже не ждёт отгрузки/)).not.toBeInTheDocument();

    finish({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "К списку заказов" }));
    expect(screen.getByRole("button", { name: /№626/ })).toBeInTheDocument();
    expect(screen.queryByText(/уже не ждёт отгрузки/)).not.toBeInTheDocument();
  });

  it("возвращает к списку с пояснением, если открытый заказ отгрузили с другого устройства", async () => {
    const user = userEvent.setup();
    let rows = [order(624), order(626)];
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/") ? paged(rows) : paged([]),
    );
    const { rerender } = render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    expect(screen.getByLabelText("Тягач")).toBeInTheDocument();

    rows = [order(626)];
    rerender(<LoaderPage />);

    expect(await screen.findByText(/Заказ №624 уже не ждёт отгрузки/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /№626/ })).toBeInTheDocument();
  });

  it("«Вставить отчёт» убран: новый заказ по отчёту проводит Telegram-бот", async () => {
    const user = userEvent.setup();
    render(<LoaderPage />);
    expect(screen.queryByRole("button", { name: /Вставить отчёт/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    expect(screen.queryByRole("button", { name: /Вставить отчёт/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /История/ }));
    expect(screen.queryByRole("button", { name: /Вставить отчёт/ })).not.toBeInTheDocument();
  });

  it("ожидающий вагонный заказ отгружается по отчёту из экрана заказа", async () => {
    const user = userEvent.setup();
    const queueApply = vi.fn();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/queue/?transport=train")
        ? paged([order(625, { transport_type: "train", client_name: "ТОО Вагон" })], { applyItems: queueApply })
        : paged([order(624)]),
    );
    mocks.railRow = order(625, { status: "shipped", transport_type: "train", shipped_at: "2026-09-19T12:00:00+05:00" });
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    await user.click(screen.getByRole("button", { name: /№625/ }));
    await user.click(screen.getByRole("button", { name: /Отгрузить по отчёту/ }));
    expect(screen.getByRole("dialog", { name: "Отчёт о вагонах" })).toHaveTextContent("заказ 625");

    await user.click(screen.getByRole("button", { name: "Провести отчёт" }));

    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    // Отчёт о вагонах уходит ботом из истории — копии у вагона нет.
    expect(screen.queryByRole("button", { name: /Скопировать/ })).not.toBeInTheDocument();
    const [left] = queueApply.mock.calls.at(-1)!;
    expect(left([order(625), order(626)]).map((row: LoaderOrder) => row.id)).toEqual([626]);
    expect(screen.queryByText(/уже не ждёт отгрузки/)).not.toBeInTheDocument();
  });

  it("без права отгрузки «Отгрузить по отчёту» не предлагает", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["loader.view", "loader.wagons"];
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№625/ }));

    expect(screen.queryByRole("button", { name: /Отгрузить по отчёту/ })).not.toBeInTheDocument();
  });

  it("отправляет отчёт Динаре из истории вагонов: всей историей или одной отгрузкой, отметка — из ответа", async () => {
    const user = userEvent.setup();
    const historyApply = vi.fn();
    const today = todayLocalIsoDate();
    const sentAt = `${today}T07:45:00+05:00`;
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/history/")
        ? paged(
            [
              order(366, {
                status: "shipped",
                transport_type: "train",
                truck_number: "12345678",
                shipped_at: `${today}T07:30:00+05:00`,
              }),
              order(365, {
                status: "shipped",
                transport_type: "train",
                shipped_at: `${today}T07:00:00+05:00`,
                report_sent_at: sentAt,
                report_deliveries: [{ to: "@dinara_k", status: "sent", status_label: "Отправлено", error: "" }],
              }),
            ],
            { applyItems: historyApply },
          )
        : paged([]),
    );
    const queued = { to: "@dinara_k", status: "queued", status_label: "В очереди", error: "" } as const;
    mocks.reportSent = { sent_at: sentAt, order_ids: [366], deliveries: [queued] };
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    // В очереди отправлять нечего: кнопка — только в истории.
    expect(screen.queryByRole("button", { name: "Отправить отчёт" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /История/ }));

    expect(screen.getByText(`Отправлено @dinara_k · ${formatTime(sentAt)}`)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Отправить отчёт" });
    // Сверху — вся история, у каждой отгрузки — своя; копия отчёта — в окне.
    expect(buttons).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Скопировать отчёт/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Скопировать все/ })).not.toBeInTheDocument();

    await user.click(buttons[0]);
    expect(screen.getByRole("dialog", { name: "Отправить отчёт" })).toHaveTextContent(
      JSON.stringify({ date_from: today, date_to: today, search: "" }),
    );
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    const [marked] = historyApply.mock.calls.at(-1)!;
    const rows = marked([order(366, { shipped_at: sentAt }), order(360)]);
    expect(rows[0]).toMatchObject({ report_deliveries: [queued], report_sent_at: sentAt });
    expect(rows[1].report_sent_at).toBeNull();
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("отчёт одной отгрузки — из её карточки", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) =>
      url?.startsWith("/loader/history/")
        ? paged([order(366, { status: "shipped", transport_type: "train", shipped_at: "2026-09-24T07:30:00+05:00" })])
        : paged([]),
    );
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getAllByRole("button", { name: "Отправить отчёт" })[1]);

    expect(screen.getByRole("dialog", { name: "Отправить отчёт" })).toHaveTextContent(JSON.stringify({ order: 366 }));
  });

  it("у фур отчёт копируется: у каждой отгрузки рядом с накладной и весь период — по фильтрам истории", async () => {
    const user = userEvent.setup();
    const today = todayLocalIsoDate();
    render(<LoaderPage />);

    // В очереди копировать нечего: кнопки — только в истории.
    expect(screen.queryByRole("button", { name: /Скопировать/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /История/ }));

    const one = screen.getByRole("button", { name: "Скопировать отчёт" });
    expect(one).toHaveAttribute("data-scope", JSON.stringify({ order: 620 }));
    expect(one.parentElement).toContainElement(screen.getByRole("button", { name: /Накладная/ }));
    const all = screen.getByRole("button", { name: "Скопировать все" });
    expect(all).toBeEnabled();
    expect(all).toHaveAttribute("data-scope", JSON.stringify({ date_from: today, date_to: today, search: "" }));

    await user.click(screen.getByRole("button", { name: "Вчера" }));
    await user.type(screen.getByLabelText("Поиск"), "403 bjn");
    const yesterday = shiftIsoDate(today, -1);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скопировать все" })).toHaveAttribute(
        "data-scope",
        JSON.stringify({ date_from: yesterday, date_to: yesterday, search: "403 bjn" }),
      ),
    );
  });

  it("за пустой период «Скопировать все» недоступно", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation(() => paged([]));
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));

    expect(screen.getByRole("button", { name: "Скопировать все" })).toBeDisabled();
  });

  it("на экране «Отгрузка подтверждена» фуры отчёт копируется сразу", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
    render(<LoaderPage />);

    await confirmTruck(user);

    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать отчёт" })).toHaveAttribute(
      "data-scope",
      JSON.stringify({ order: 624 }),
    );
  });

  it("у фур отчёта о вагонах нет", async () => {
    const user = userEvent.setup();
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));

    expect(screen.getByRole("button", { name: /Накладная/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отправить отчёт/ })).not.toBeInTheDocument();
  });

  describe("склад отгрузки у фуры", () => {
    it("сначала сверяет склады тем же номером; склад один — отгружает без опросника", async () => {
      const user = userEvent.setup();
      mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
      render(<LoaderPage />);

      await confirmTruck(user);

      expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
      // Параметры проверки — ровно то, что уйдёт в отгрузку.
      expect(mocks.get).toHaveBeenCalledWith("/loader/orders/624/dispatch-sources/", {
        params: { truck_number: "403BJN13" },
      });
      // Выбирать не из чего: sources не уходят, всё со «Склада отгрузки».
      expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403BJN13" });
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
    });

    it("два склада: опросник с первого товара, отгрузка уходит с ответами грузчика", async () => {
      const user = userEvent.setup();
      mocks.get.mockResolvedValue({ data: SPLIT });
      mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
      render(<LoaderPage />);

      await confirmTruck(user);

      const sheet = await screen.findByRole("dialog", SHEET);
      // Выбор всегда сознательный: без ответа ничего не отгружается.
      expect(mocks.post).not.toHaveBeenCalled();
      expect(sheet).toHaveTextContent("начало: первый товар");
      expect(sheet).toHaveTextContent("товары: Д1с · Красный 50 кг — 2");
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Отгрузить" }));

      await waitFor(() =>
        expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", {
          truck_number: "403BJN13",
          sources: SPLIT_SOURCES,
        }),
      );
      expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
    });

    it("ошибку номера на проверке складов показывает на экране заказа, опросник не открывает", async () => {
      const user = userEvent.setup();
      mocks.get.mockRejectedValue(
        codedError("truck_number_locked", "Номер машины нельзя изменить после прибытия или начала погрузки"),
      );
      render(<LoaderPage />);

      await confirmTruck(user);

      expect(
        await screen.findByText("Номер машины нельзя изменить после прибытия или начала погрузки"),
      ).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();
      // Экран заказа на месте: номер можно исправить и нажать снова.
      expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeEnabled();
    });

    it("склад включили между проверкой и отгрузкой — открывает опросник с пояснением", async () => {
      const user = userEvent.setup();
      mocks.get.mockResolvedValueOnce({ data: ONE_WAREHOUSE }).mockResolvedValueOnce({ data: SPLIT });
      mocks.post.mockRejectedValueOnce(
        codedError("sources_required", "Выберите, с какого склада отгрузка (если окна выбора нет — обновите страницу)"),
      );
      render(<LoaderPage />);

      await confirmTruck(user);

      const sheet = await screen.findByRole("dialog", SHEET);
      expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403BJN13" });
      expect(sheet).toHaveTextContent(SOURCES_CHANGED);
      expect(sheet).toHaveTextContent("начало: первый товар");
      // Вместо отказа сервера на экране — вопрос в листе.
      expect(screen.queryByText(/обновите страницу/)).not.toBeInTheDocument();
    });

    it("состав заказа изменился после ответа — спрашивает заново с первого товара, даже со сводки", async () => {
      const user = userEvent.setup();
      const changed: DispatchSources = { ...SPLIT, products: [{ ...SPLIT.products[0], bags: 3 }] };
      mocks.get
        .mockResolvedValueOnce({ data: SPLIT })
        .mockResolvedValueOnce({ data: SPLIT })
        .mockResolvedValueOnce({ data: changed });
      mocks.post.mockRejectedValueOnce(
        codedError("sources_mismatch", "Состав заказа изменился — ответьте заново: «Д1с»: в заказе 3, выбрано 2"),
      );
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      // Крестик закрывает лист, ответы остаются: следующее нажатие — сразу сводка.
      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
      const summary = await screen.findByRole("dialog", SHEET);
      expect(summary).toHaveTextContent("начало: сводка");
      await user.click(within(summary).getByRole("button", { name: "Отгрузить" }));

      expect(await screen.findByText(SOURCES_CHANGED)).toBeInTheDocument();
      const restarted = screen.getByRole("dialog", SHEET);
      expect(mocks.get).toHaveBeenCalledTimes(3);
      expect(mocks.get).toHaveBeenLastCalledWith("/loader/orders/624/dispatch-sources/", {
        params: { truck_number: "403BJN13" },
      });
      expect(restarted).toHaveTextContent("товары: Д1с · Красный 50 кг — 3");
      // Прежние ответы сброшены, опрос — с первого товара, а не со сводки.
      expect(restarted).toHaveTextContent("ответы: {}");
      expect(restarted).toHaveTextContent("начало: первый товар");
      // Лист остался открыт: ни ошибки на экране заказа, ни сверки очереди.
      expect(screen.queryByText(/выбрано 2/)).not.toBeInTheDocument();
      expect(mocks.refresh).not.toHaveBeenCalled();
    });

    it("прочая ошибка закрывает опросник, ошибка — на экране; ответы целы — снова нажал и сразу сводка", async () => {
      const user = userEvent.setup();
      mocks.get.mockResolvedValue({ data: SPLIT });
      mocks.post
        .mockRejectedValueOnce(new Error("Нет связи с сервером. Проверьте интернет и повторите."))
        .mockResolvedValueOnce({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Отгрузить" }));

      expect(await screen.findByText("Нет связи с сервером. Проверьте интернет и повторите.")).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
      // Заказ мог уехать с другого устройства — страница сверяется с очередью.
      expect(mocks.refresh).toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
      const summary = await screen.findByRole("dialog", SHEET);
      expect(summary).toHaveTextContent("начало: сводка");
      expect(summary).toHaveTextContent(`ответы: ${JSON.stringify(SPLIT_ANSWERS)}`);
      await user.click(within(summary).getByRole("button", { name: "Отгрузить" }));

      await waitFor(() =>
        expect(mocks.post).toHaveBeenLastCalledWith("/loader/orders/624/dispatch/", {
          truck_number: "403BJN13",
          sources: SPLIT_SOURCES,
        }),
      );
      expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    });

    it("не удалось заново спросить склады — ошибка в листе, ответы целы", async () => {
      const user = userEvent.setup();
      mocks.get
        .mockResolvedValueOnce({ data: SPLIT })
        .mockRejectedValueOnce(new Error("Нет связи с сервером. Проверьте интернет и повторите."));
      mocks.post.mockRejectedValueOnce(codedError("warehouse_inactive", "Склад «Мельница 2» отключён"));
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Отгрузить" }));

      expect(
        await within(sheet).findByText("Нет связи с сервером. Проверьте интернет и повторите."),
      ).toBeInTheDocument();
      // «Отгрузить» в листе повторит проверку с теми же ответами.
      expect(sheet).toHaveTextContent(`ответы: ${JSON.stringify(SPLIT_ANSWERS)}`);
      expect(screen.queryByText(SOURCES_CHANGED)).not.toBeInTheDocument();
      // На экране заказа ошибки нет — она только в листе.
      expect(screen.getAllByText("Нет связи с сервером. Проверьте интернет и повторите.")).toHaveLength(1);
    });

    it("склад выключили и остался один — лист закрывается, пояснение на экране, следующее нажатие отгружает без опросника", async () => {
      const user = userEvent.setup();
      mocks.get
        .mockResolvedValueOnce({ data: SPLIT })
        .mockResolvedValueOnce({ data: ONE_WAREHOUSE })
        .mockResolvedValueOnce({ data: ONE_WAREHOUSE });
      mocks.post
        .mockRejectedValueOnce(codedError("warehouse_inactive", "Склад «Мельница 2» отключён"))
        .mockResolvedValueOnce({ data: order(624, { status: "shipped", truck_number: "403BJN13" }) });
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Отгрузить" }));

      // Выбирать больше не из чего: листа нет, пояснение — на экране заказа.
      expect(await screen.findByText(SOURCES_CHANGED)).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
      expect(screen.queryByText(/отключён/)).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

      expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
      expect(mocks.get).toHaveBeenCalledTimes(3);
      // Всё со «Склада отгрузки»: sources не уходят.
      expect(mocks.post).toHaveBeenLastCalledWith("/loader/orders/624/dispatch/", { truck_number: "403BJN13" });
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
    });

    it("лист закрыли во время отгрузки, склады заново спросить не удалось — ошибка на экране заказа", async () => {
      const user = userEvent.setup();
      let reject!: (cause: unknown) => void;
      mocks.get
        .mockResolvedValueOnce({ data: SPLIT })
        .mockRejectedValueOnce(new Error("Нет связи с сервером. Проверьте интернет и повторите."));
      mocks.post.mockReturnValueOnce(new Promise((_, fail) => (reject = fail)));
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Отгрузить" }));
      // Крестик работает и пока отгрузка в полёте.
      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();

      reject(codedError("warehouse_inactive", "Склад «Мельница 2» отключён"));

      // Лист закрыт — ошибка не теряется в нём, а видна рядом с номером.
      expect(await screen.findByText("Нет связи с сервером. Проверьте интернет и повторите.")).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
      expect(mocks.refresh).toHaveBeenCalled();
    });

    it("пока проверяются склады и открыт опросник, очередь не опрашивается", async () => {
      const user = userEvent.setup();
      let answer!: (value: unknown) => void;
      mocks.get.mockReturnValue(new Promise((resolve) => (answer = resolve)));
      render(<LoaderPage />);

      await confirmTruck(user);
      // До листа кнопка говорит, что идёт проверка складов, а не отгрузка.
      expect(screen.getByRole("button", { name: /Проверяем склады…/ })).toBeDisabled();
      expect(lastPoll().active).toBe(false);

      answer({ data: SPLIT });
      const sheet = await screen.findByRole("dialog", SHEET);
      expect(lastPoll().active).toBe(false);
      expect(mocks.post).not.toHaveBeenCalled();

      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(lastPoll().active).toBe(true);
      expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeEnabled();
    });

    it("пока открыт опросник, пропавший из очереди заказ не уводит с экрана — пояснение после закрытия", async () => {
      const user = userEvent.setup();
      let rows = [order(624), order(626)];
      mocks.paged.mockImplementation((url: string | null) =>
        url?.startsWith("/loader/queue/") ? paged(rows) : paged([]),
      );
      mocks.get.mockResolvedValue({ data: SPLIT });
      const { rerender } = render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);

      rows = [order(626)];
      rerender(<LoaderPage />);
      expect(screen.getByRole("dialog", SHEET)).toBeInTheDocument();
      expect(screen.getByLabelText("Тягач")).toBeInTheDocument();
      expect(screen.queryByText(/уже не ждёт отгрузки/)).not.toBeInTheDocument();

      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      expect(await screen.findByText(/Заказ №624 уже не ждёт отгрузки/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /№626/ })).toBeInTheDocument();
    });

    it("«Назад» забывает ответы опросника: заказ снова спрашивается с первого товара", async () => {
      const user = userEvent.setup();
      mocks.get.mockResolvedValue({ data: SPLIT });
      render(<LoaderPage />);

      await confirmTruck(user);
      const sheet = await screen.findByRole("dialog", SHEET);
      await user.click(within(sheet).getByRole("button", { name: "Ответить" }));
      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      await user.click(screen.getByRole("button", { name: "Назад" }));

      await confirmTruck(user);
      const fresh = await screen.findByRole("dialog", SHEET);
      expect(fresh).toHaveTextContent("начало: первый товар");
      expect(fresh).toHaveTextContent("ответы: {}");
    });

    it("пока проверяются склады, «Назад» не уводит: опросник не всплывёт у другого заказа, очередь опрашивается", async () => {
      const user = userEvent.setup();
      mocks.paged.mockImplementation((url: string | null) =>
        url?.startsWith("/loader/queue/") ? paged([order(624), order(626)]) : paged([]),
      );
      let answer!: (value: unknown) => void;
      mocks.get.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
      render(<LoaderPage />);

      await confirmTruck(user);
      // Проверка ушла: ответ сервера относится к этому заказу — уйти с него до ответа нельзя.
      expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Назад" }));

      answer({ data: SPLIT });
      const sheet = await screen.findByRole("dialog", SHEET);
      // Опросник — на экране проверенного заказа.
      expect(screen.getByText("№624")).toBeInTheDocument();
      await user.click(within(sheet).getByRole("button", { name: "Закрыть" }));
      await user.click(screen.getByRole("button", { name: "Назад" }));
      expect(lastPoll().active).toBe(true);

      await user.click(screen.getByRole("button", { name: /№626/ }));
      expect(screen.getByText("№626")).toBeInTheDocument();
      expect(screen.queryByRole("dialog", SHEET)).not.toBeInTheDocument();
      expect(lastPoll().active).toBe(true);
      expect(mocks.get).toHaveBeenCalledTimes(1);
      expect(mocks.post).not.toHaveBeenCalled();
    });
  });
});
