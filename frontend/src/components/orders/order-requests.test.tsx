import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { OrderRequestsSection } from "./order-requests";
import { ORDER_REQUESTS_URL, useOrderRequests } from "./use-order-requests";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  poll: async () => {},
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<void>, _interval: number, active: boolean) => {
    if (active) mocks.poll = poll;
  },
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

function Harness({ onChanged }: { onChanged?: () => unknown }) {
  const requests = useOrderRequests(true, onChanged);
  return <OrderRequestsSection requests={requests} />;
}

const request = {
  id: 621,
  department: "",
  department_name: "",
  client_name: "Клиент",
  status: "pending",
  currency: "KZT",
  total_amount: "0",
  paid_total: "0",
  created_at: "2026-09-17T08:00:00Z",
  items: [{ id: 7, product: 1, quantity: 2, product_label: "Мука" }],
};
let includeRequest = true;

beforeEach(() => {
  includeRequest = true;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/")
      return {
        data: { results: includeRequest ? [request] : [], count: includeRequest ? 1 : 0, next: null },
      };
    if (url.pathname === "/departments/") return { data: [{ id: 1, code: "main", name: "Мельница", is_active: true }] };
    return { data: [] };
  });
});

it("lists client requests of the shared queue and rejects one", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  expect(await screen.findByRole("link", { name: "Заказ #621" })).toHaveAttribute(
    "href",
    "/orders/621?back=%2Forders%3Ftab%3Drequests",
  );
  expect(mocks.get).toHaveBeenCalledWith(`${ORDER_REQUESTS_URL}&page=1&page_size=50`, expect.anything());
  expect(screen.getByText("Ждёт отдела")).toBeInTheDocument();
  expect(screen.getByText("Мука × 2")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Отклонить" }));
  const dialog = await screen.findByRole("dialog", { name: "Отклонить заявку #621" });
  expect(dialog).toBeInTheDocument();
});

it("keeps entered confirmation data when a background refresh removes the row from the page", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn();
  render(<Harness onChanged={onChanged} />);
  await user.click(await screen.findByRole("button", { name: "Проверить и подтвердить" }));
  await user.selectOptions(await screen.findByRole("combobox", { name: "Отдел продаж" }), "main");
  await user.type(screen.getByRole("spinbutton", { name: "Цена: Мука" }), "1234");
  includeRequest = false;
  await act(async () => {
    await mocks.poll();
  });
  expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toHaveValue("main");
  expect(screen.getByRole("spinbutton", { name: "Цена: Мука" })).toHaveValue(1234);
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  // Клиент без отдела: сперва подтверждаем, что закрепляем его за отделом.
  await user.click(screen.getByRole("button", { name: "Да, закрепить и подтвердить" }));
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Отдел продаж" })).not.toBeInTheDocument());
  expect(mocks.post).toHaveBeenCalledWith("/orders/621/confirm/", { department: "main", prices: { "7": "1234" } });
  // Подтверждение обновляет и список заказов со сводкой на странице.
  expect(onChanged).toHaveBeenCalled();
  expect(await screen.findByText("Нет заявок, ожидающих подтверждения.")).toBeInTheDocument();
});

it("shows a confirmation error inside the dialog", async () => {
  const user = userEvent.setup();
  mocks.post.mockRejectedValue(new Error("Цена не указана"));
  render(<Harness />);
  await user.click(await screen.findByRole("button", { name: "Проверить и подтвердить" }));
  const dialog = await screen.findByRole("dialog", { name: /Заказ #621/ });
  await user.selectOptions(within(dialog).getByRole("combobox", { name: "Отдел продаж" }), "main");
  await user.type(within(dialog).getByRole("spinbutton", { name: "Цена: Мука" }), "10");
  await user.click(within(dialog).getByRole("button", { name: "Подтвердить заказ" }));
  await user.click(within(dialog).getByRole("button", { name: "Да, закрепить и подтвердить" }));
  expect(await within(dialog).findByText("Цена не указана")).toBeInTheDocument();
});
