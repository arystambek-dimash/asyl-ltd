import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LoaderOrder } from "@/lib/loader";

import LoaderPage from "./page";

const mocks = vi.hoisted(() => ({
  permissions: ["loader.view", "loader.confirm"] as string[],
  paged: vi.fn(),
  post: vi.fn(),
  openWaybill: vi.fn(),
  reload: vi.fn(),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.paged }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/loader", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/loader")>()),
  openWaybill: mocks.openWaybill,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, tabs, footer }: { children: ReactNode; tabs?: ReactNode; footer?: ReactNode }) => (
    <main>
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

function paged(items: LoaderOrder[]) {
  return {
    items,
    count: items.length,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    reload: mocks.reload,
    loadMore: vi.fn(),
  };
}

describe("LoaderPage", () => {
  beforeEach(() => {
    mocks.permissions = ["loader.view", "loader.confirm"];
    mocks.post.mockReset();
    mocks.openWaybill.mockReset().mockResolvedValue(undefined);
    mocks.reload.mockReset();
    mocks.paged.mockReset().mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/"))
        return paged([order(624), order(625, { transport_type: "train", client_name: "ТОО Вагон" })]);
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
    await user.type(screen.getByLabelText("Номер машины"), "403 bjn 13");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403 BJN 13" }),
    );
    expect(await screen.findByText("Отгрузка подтверждена")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Печать накладной/ }));
    expect(mocks.openWaybill).toHaveBeenCalledWith(624);

    await user.click(screen.getByRole("button", { name: "К списку заказов" }));
    expect(screen.queryByText("Отгрузка подтверждена")).not.toBeInTheDocument();
  });

  it("без права подтверждения показывает очередь и заказ без кнопки отгрузки", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["loader.view"];
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
    const field = screen.getByLabelText("Номер машины");
    expect(field).toHaveValue("111 AAA 01");
    await user.clear(field);
    await user.type(field, "403 bjn 13");
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));

    expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403 BJN 13" });
  });

  it("отменяет ошибочную отгрузку сразу после неё и из истории", async () => {
    const user = userEvent.setup();
    mocks.paged.mockImplementation((url: string | null) => {
      if (url?.startsWith("/loader/queue/")) return paged([order(624, { truck_number: "111 AAA 01" })]);
      if (url?.startsWith("/loader/history/"))
        return paged([order(620, { status: "shipped", shipped_at: "2026-09-18T11:31:00+05:00", can_rollback: true })]);
      return paged([]);
    });
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", can_rollback: true }) });
    render(<LoaderPage />);

    await user.click(screen.getByRole("button", { name: /№624/ }));
    await user.click(screen.getByRole("button", { name: /Подтвердить отгрузку/ }));
    await user.click(await screen.findByRole("button", { name: /Отменить отгрузку/ }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/rollback/", {}));
    expect(await screen.findByText(/Отгрузка заказа №624 отменена/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /История/ }));
    await user.click(screen.getByRole("button", { name: /Отменить$/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/loader/orders/620/rollback/", {}));
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
      expect.stringMatching(/^\/loader\/history\/\?date_from=\d{4}-\d{2}-\d{2}&date_to=/),
    );
    await user.click(screen.getByRole("button", { name: /Накладная/ }));
    expect(mocks.openWaybill).toHaveBeenCalledWith(620);
  });
});
