import { render, screen, waitFor, within } from "@testing-library/react";
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

  it("выбирает заказ и подтверждает отгрузку одной большой кнопкой, затем печатает накладную", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: order(624, { status: "shipped", truck_number: "403 BJN 13" }) });
    render(<LoaderPage />);

    const confirm = screen.getByRole("button", { name: /Подтвердить отгрузку/ });
    expect(confirm).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /№624/ }));
    await user.type(screen.getByLabelText("Номер машины"), "403 bjn 13");
    await user.click(screen.getByRole("button", { name: "Подтвердить отгрузку №624" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/loader/orders/624/dispatch/", { truck_number: "403 BJN 13" }),
    );
    const footer = screen.getByRole("contentinfo");
    expect(await within(footer).findByText("Заказ №624 отгружен")).toBeInTheDocument();
    await user.click(within(footer).getByRole("button", { name: /Печать накладной/ }));
    expect(mocks.openWaybill).toHaveBeenCalledWith(624);
  });

  it("без права подтверждения показывает очередь без кнопки", () => {
    mocks.permissions = ["loader.view"];
    render(<LoaderPage />);

    expect(screen.getByText(/№624 · ИП Мурат/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Подтвердить отгрузку/ })).not.toBeInTheDocument();
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
