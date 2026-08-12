import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TransactionsSection } from "@/components/transactions-section";
import type { Department } from "@/lib/types";

const useApiMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));

const departments = [
  {
    id: 1,
    code: "north",
    name: "Север",
    color: "#315FD5",
    is_active: true,
    is_default: true,
    order_count: 0,
    created_at: "2026-01-01T00:00:00Z",
  },
] satisfies Department[];

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
