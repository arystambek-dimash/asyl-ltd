import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import OrdersPage from "./page";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { permissions: ["orders.view"] }, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: () => "Ошибка",
  isCanceledRequest: () => false,
}));

beforeEach(() => {
  mocks.get.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/") {
      const page = Number(url.searchParams.get("page"));
      return {
        data: {
          results: [
            {
              id: page,
              client_name: "Клиент",
              truck_number: "934PPB13",
              currency: "KZT",
              status: "confirmed",
              total_amount: "100",
              paid_total: "0",
              payment_status: "unpaid",
              items: [],
              created_at: "2026-09-01T10:00:00Z",
            },
          ],
          count: 70,
          next: page < 2 ? "next" : null,
        },
      };
    }
    return { data: [] };
  });
});

it("keeps searching and sorting paginated and loads later results only on request", async () => {
  const user = userEvent.setup();
  render(<OrdersPage />);
  const orderUrls = () =>
    mocks.get.mock.calls
      .map(([url]) => new URL(String(url), "http://localhost"))
      .filter((url) => url.pathname === "/orders/");
  await waitFor(() => expect(orderUrls()).toHaveLength(1));
  expect(orderUrls()[0].searchParams.get("ordering")).toBe("-id");
  await user.type(screen.getByPlaceholderText("Поиск по клиенту, номеру или #ID"), "934");
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("search")).toBe("934"));
  expect(orderUrls()).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Сумма" }));
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("ordering")).toBe("amount"));
  expect(
    orderUrls().every((url) => url.searchParams.get("page") === "1" && url.searchParams.get("page_size") === "50"),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: /Показать ещё/ }));
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("page")).toBe("2"));
  expect(orderUrls().at(-1)?.searchParams.get("search")).toBe("934");
  expect(orderUrls().at(-1)?.searchParams.get("ordering")).toBe("amount");
});

it("separates new and reviewed requests on the server and defers analytics", async () => {
  const user = userEvent.setup();
  render(<OrdersPage />);
  await user.click(screen.getByRole("tab", { name: /Новые заявки/ }));
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => String(url).includes("review_stage=new"))).toBe(true),
  );
  await user.click(screen.getByRole("tab", { name: /На рассмотрении/ }));
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => String(url).includes("review_stage=review"))).toBe(true),
  );
  expect(mocks.get.mock.calls.some(([url]) => String(url).includes("department-summary"))).toBe(false);
  await user.click(screen.getByText("Аналитика и сравнение отделов"));
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => String(url).includes("department-summary"))).toBe(true),
  );
});
