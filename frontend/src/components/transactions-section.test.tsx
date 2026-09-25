import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TransactionsSection } from "@/components/transactions-section";
import { makeDepartment } from "@/test-utils/factories";

const useApiMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));

const departments = [makeDepartment({ code: "north", name: "Север" })];

describe("TransactionsSection department filter", () => {
  beforeEach(() => {
    useApiMock.mockReset();
    useApiMock.mockReturnValue({
      data: null,
      loading: false,
      error: "",
      reload: vi.fn(),
    });
  });

  it("requests the first transaction page for the selected order department", async () => {
    const user = userEvent.setup();
    render(<TransactionsSection canConfirm={false} canCreate={false} departments={departments} />);

    await user.click(screen.getByRole("button", { name: /Отдел:.*Все/ }));
    await user.click(screen.getByRole("option", { name: "Север" }));

    await waitFor(() => {
      expect(useApiMock).toHaveBeenCalledWith("/payment-transactions/?page=1&page_size=50&search=&department=north");
    });
  });
});

describe("TransactionsSection payment method", () => {
  it("shows the method, stage and filter labels the API sends", async () => {
    // Подписи — из labels.py на бэке: журнал говорит теми же словами, что выписки.
    const page = {
      results: [
        {
          id: 7,
          order: 3,
          amount: "500",
          currency: "KZT",
          method: "kaspi",
          method_label: "QR",
          status: "confirmed",
          status_label: "Оплачено",
          effective_status: "confirmed",
          effective_status_label: "Оплачено",
          paid_at: "2026-09-20T10:00:00",
          client_name: "Клиент",
        },
      ],
      count: 1,
      page: 1,
      pages: 1,
      status_counts: { confirmed: 1 },
      status_labels: { requested: "Ожидает", received: "В кассе", confirmed: "Оплачено", rejected: "Отклонено" },
      summary: {
        paid_by_currency: { KZT: "500", USD: "0" },
        refunded_by_currency: { KZT: "0", USD: "0" },
        paid_by_method: {},
        method_labels: {},
      },
    };
    // Страница приходит после монтирования, как у настоящего useApi.
    let data: typeof page | null = null;
    useApiMock.mockReset();
    useApiMock.mockImplementation(() => ({ data, loading: false, error: "", reload: vi.fn() }));
    const view = render(<TransactionsSection canConfirm={false} canCreate={false} departments={departments} />);
    data = page;
    view.rerender(<TransactionsSection canConfirm={false} canCreate={false} departments={departments} />);

    expect(await screen.findByRole("cell", { name: "QR" })).toBeInTheDocument();
    expect(screen.getAllByText("Оплачено").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /В кассе/ })).toBeInTheDocument();
  });
});
