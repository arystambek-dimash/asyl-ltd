import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LoaderOrder } from "@/lib/loader";
import { formatTime, todayLocalIsoDate } from "@/lib/utils";
import type { WagonReportScope, WagonReportSent } from "@/lib/wagon-report";

import LoaderPage from "./page";

const ALL_AREAS = ["loader.view", "loader.confirm", "loader.trucks", "loader.wagons"];

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  paged: vi.fn(),
  post: vi.fn(),
  openWaybill: vi.fn(),
  reload: vi.fn(),
  refresh: vi.fn(),
  applyItems: vi.fn(),
  polling: [] as { poll: () => Promise<unknown>; interval: number; active: boolean }[],
  apiUrls: [] as (string | null)[],
  showToast: vi.fn(),
  railRow: null as LoaderOrder | null,
  reportSent: null as WagonReportSent | null,
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
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/loader", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/loader")>()),
  openWaybill: mocks.openWaybill,
}));
vi.mock("@/lib/toast", () => ({ showToast: mocks.showToast }));
// Лист отчёта проверен своими тестами; здесь — как страница его открывает и применяет ответ.
vi.mock("@/components/loader/rail-report-sheet", () => ({
  RailReportSheet: ({ orderId, onApplied }: { orderId: number | null; onApplied: (row: LoaderOrder) => void }) => (
    <div role="dialog" aria-label="Отчёт о вагонах">
      <span>{orderId === null ? "новый отчёт" : `заказ ${orderId}`}</span>
      <button type="button" onClick={() => onApplied(mocks.railRow!)}>
        Провести отчёт
      </button>
    </div>
  ),
}));
// Окно отправки проверено своими тестами; здесь — с чем страница его открывает и как применяет ответ.
vi.mock("@/components/loader/wagon-report-modal", () => ({
  WagonReportModal: ({ scope, onSent }: { scope: WagonReportScope; onSent: (sent: WagonReportSent) => void }) => (
    <div role="dialog" aria-label="Отправить отчёт">
      <span>{JSON.stringify(scope)}</span>
      <button type="button" onClick={() => onSent(mocks.reportSent!)}>
        Отправить Динаре
      </button>
    </div>
  ),
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({
    children,
    tabs,
    actions,
    footer,
  }: {
    children: ReactNode;
    tabs?: ReactNode;
    actions?: ReactNode;
    footer?: ReactNode;
  }) => (
    <main>
      {actions}
      {tabs}
      {children}
      <footer>{footer}</footer>
    </main>
  ),
}));

const order = (id: number, fields: Partial<LoaderOrder> = {}): LoaderOrder => ({
  id,
  status: "confirmed",
  transport_type: "truck",
  truck_number: "",
  currency: "KZT",
  arrival_date: null,
  created_at: "2026-09-16T10:00:00+05:00",
  client_name: "ИП Мурат",
  items: [{ label: "Д1с · Красный 50 кг", quantity: 2, weight_kg: "50.00", unit_price: "10000.00" }],
  bags: 2,
  total_kg: "100.00",
  total_amount: "20000.00",
  shipped_at: null,
  ...fields,
});

function paged(items: LoaderOrder[], fields: { refreshError?: string; applyItems?: typeof mocks.applyItems } = {}) {
  return {
    items,
    count: items.length,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    refreshError: "",
    reload: mocks.reload,
    refresh: mocks.refresh,
    loadMore: vi.fn(),
    applyItems: mocks.applyItems,
    ...fields,
  };
}

const queueUrls = () =>
  mocks.paged.mock.calls.map(([url]) => url).filter((url) => typeof url === "string" && url.includes("queue"));
const lastPoll = () => mocks.polling.at(-1)!;

describe("LoaderPage", () => {
  beforeEach(() => {
    mocks.permissions = ALL_AREAS;
    mocks.post.mockReset();
    mocks.openWaybill.mockReset().mockResolvedValue(undefined);
    mocks.reload.mockReset();
    mocks.refresh.mockReset().mockResolvedValue(undefined);
    mocks.applyItems.mockReset();
    mocks.polling = [];
    mocks.apiUrls = [];
    mocks.showToast.mockReset();
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
    await user.click(screen.getByRole("button", { name: /№624 · ИП Мурат/ }));

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

    expect(screen.getByText(/№624 · ИП Мурат/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /№624 · ИП Мурат/ }));
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

  it("у вагона уходит только его номер", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: order(625, { status: "shipped", transport_type: "train" }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    await user.click(screen.getByRole("button", { name: /№625/ }));
    await user.type(screen.getByLabelText("Номер вагона"), "0012 3456");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/625/dispatch/", { truck_number: "00123456" });
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
        return paged([order(624, { truck_number: "111 AAA 01", arrival_date: today })], { applyItems: queueApply });
      if (url?.startsWith("/loader/history/"))
        return paged([order(620, { status: "shipped", shipped_at: "2026-09-18T11:31:00+05:00", can_rollback: true })], {
          applyItems: historyApply,
        });
      return paged([]);
    });
    mocks.post.mockImplementation(async (url: string) => {
      if (url === "/loader/orders/624/dispatch/")
        return { data: order(624, { status: "shipped", can_rollback: true, arrival_date: today }) };
      if (url === "/loader/orders/624/rollback/") return { data: order(624, { arrival_date: today }) };
      // Заказ 620 ждали на прошлой неделе: под «Сегодня» он в очередь не встаёт.
      return { data: order(620, { arrival_date: "2000-01-01" }) };
    });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
    await user.click(await screen.findByRole("button", { name: /Отменить отгрузку/ }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/rollback/", {}));
    expect(await screen.findByText(/Отгрузка заказа №624 отменена/)).toBeInTheDocument();
    // Ответ отмены — строка очереди: заказ встаёт на своё место без перезагрузки.
    const [putBack] = queueApply.mock.calls.at(-1)!;
    expect(putBack([order(626, { arrival_date: today })]).map((row: LoaderOrder) => row.id)).toEqual([624, 626]);

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: /Отменить$/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/620/rollback/", {}));
    const [fromHistory] = historyApply.mock.calls.at(-1)!;
    expect(fromHistory([order(620), order(621)]).map((row: LoaderOrder) => row.id)).toEqual([621]);
    const [notShown] = queueApply.mock.calls.at(-1)!;
    expect(notShown([order(626, { arrival_date: today })]).map((row: LoaderOrder) => row.id)).toEqual([626]);
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
    mocks.post.mockResolvedValue({ data: order(620, { arrival_date: todayLocalIsoDate() }) });
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
              paid_total: "20000.00",
              remaining_amount: "0.00",
            }),
            order(701, {
              truck_number: "222 BBB 02",
              payment_status: "unpaid",
              paid_total: "0.00",
              remaining_amount: "20000.00",
            }),
          ])
        : paged([]),
    );
    render(<LoaderPage />);

    expect(screen.getByText("Оплачен")).toBeInTheDocument();
    expect(screen.getByText(/Не оплачен · 20 000 ₸/)).toBeInTheDocument();

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

    // Очередь спрашивается за сегодня; параллельный вызов истории с null не мешает.
    const lastQueueUrl = () => queueUrls().at(-1);
    const today = new Date().toISOString().slice(0, 10);
    expect(lastQueueUrl()).toBe(`/loader/queue/?transport=truck&day=${today}`);
    expect(screen.getByRole("button", { name: "Сегодня" })).toHaveAttribute("aria-pressed", "true");

    await user.click(await screen.findByRole("button", { name: /Просрочено · 3/ }));
    expect(lastQueueUrl()).toBe("/loader/queue/?transport=truck&overdue=1");

    await user.click(screen.getByRole("button", { name: "Все" }));
    expect(lastQueueUrl()).toBe("/loader/queue/?transport=truck");
  });

  it("группирует очередь по дням: просрочка отдельно от сегодняшних", async () => {
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/"))
        return paged([
          order(700, { arrival_date: "2000-01-01" }),
          order(701, { arrival_date: new Date().toISOString().slice(0, 10) }),
        ]);
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
    // Карточка вагона: номер, тонны и мешки, клиент.
    expect(screen.getByText("68 т")).toBeInTheDocument();
    expect(screen.getByText("1360")).toBeInTheDocument();
    expect(screen.getByText(/№625 · ТОО Вагон/)).toBeInTheDocument();
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
    expect(screen.getByText(/№625 · ТОО Вагон/)).toBeInTheDocument();
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
    expect(screen.getByText(/№624 · ИП Мурат/)).toBeInTheDocument();
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

  it("«Вставить отчёт» только во вкладке «Вагоны»: проведённый отчёт — экран отгрузки и строка истории", async () => {
    const user = userEvent.setup();
    const queueApply = vi.fn();
    const historyApply = vi.fn();
    const today = todayLocalIsoDate();
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/"))
        return paged([order(625, { transport_type: "train" })], { applyItems: queueApply });
      if (url?.startsWith("/loader/history/")) return paged([], { applyItems: historyApply });
      return paged([]);
    });
    mocks.railRow = order(700, {
      status: "shipped",
      transport_type: "train",
      shipped_at: `${today}T12:00:00+05:00`,
      rail_station: "Раустан",
      wagons: [
        { number: "28087658", product_label: "Д1с", bags: 1360, weight_kg: "68000.00" },
        { number: "28087666", product_label: "Д1с", bags: 1360, weight_kg: "68000.00" },
      ],
      bags: 2720,
      total_kg: "136000.00",
    });
    render(<LoaderPage />);
    expect(screen.queryByRole("button", { name: /Вставить отчёт/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: /Вставить отчёт/ }));
    expect(screen.getByRole("dialog", { name: "Отчёт о вагонах" })).toHaveTextContent("новый отчёт");
    // Пока лист открыт, очередь не опрашивается.
    expect(lastPoll().active).toBe(false);

    await user.click(screen.getByRole("button", { name: "Провести отчёт" }));

    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    expect(screen.getByText("2 вагона · ст. Раустан")).toBeInTheDocument();
    expect(screen.getByText("28087666")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Отчёт о вагонах" })).not.toBeInTheDocument();
    const [shown] = historyApply.mock.calls.at(-1)!;
    expect(shown([order(690, { shipped_at: `${today}T09:00:00+05:00` })]).map((row: LoaderOrder) => row.id)).toEqual([
      700, 690,
    ]);
    const [left] = queueApply.mock.calls.at(-1)!;
    expect(left([order(700), order(625)]).map((row: LoaderOrder) => row.id)).toEqual([625]);
    expect(mocks.reload).not.toHaveBeenCalled();
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
    // Проверить отчёт может и тот, кто не отгружает, — лист сам скажет, кто проведёт.
    await user.click(screen.getByRole("button", { name: /Назад/ }));
    expect(screen.getByRole("button", { name: /Вставить отчёт/ })).toBeInTheDocument();
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
                report_sent_to: "Динаре",
                report_status: "link",
              }),
            ],
            { applyItems: historyApply },
          )
        : paged([]),
    );
    mocks.reportSent = {
      status: "queued",
      status_label: "В очереди",
      sent_at: sentAt,
      order_ids: [366],
      recipient: { name: "Динара", to: "Динаре", phone: "77011234567" },
      error: "",
    };
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /Вагоны/ }));
    // В очереди отправлять нечего: кнопка — только в истории.
    expect(screen.queryByRole("button", { name: "Отправить отчёт" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /История/ }));

    expect(screen.getByText(`Отправлено Динаре ${formatTime(sentAt)}`)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Отправить отчёт" });
    // Сверху — вся история, у каждой отгрузки — своя; копия отчёта — в окне.
    expect(buttons).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Скопировать отчёт/ })).not.toBeInTheDocument();

    await user.click(buttons[0]);
    expect(screen.getByRole("dialog", { name: "Отправить отчёт" })).toHaveTextContent(
      JSON.stringify({ date_from: today, date_to: today, search: "" }),
    );
    await user.click(screen.getByRole("button", { name: "Отправить Динаре" }));
    const [marked] = historyApply.mock.calls.at(-1)!;
    const rows = marked([order(366, { shipped_at: sentAt }), order(360)]);
    expect(rows[0]).toMatchObject({ report_status: "queued", report_sent_to: "Динаре", report_sent_at: sentAt });
    expect(rows[1].report_sent_at).toBeUndefined();
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

  it("у фур отчёта о вагонах нет", async () => {
    const user = userEvent.setup();
    render(<LoaderPage />);

    await user.click(screen.getByRole("tab", { name: /История/ }));

    expect(screen.getByRole("button", { name: /Накладная/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отправить отчёт/ })).not.toBeInTheDocument();
  });
});
