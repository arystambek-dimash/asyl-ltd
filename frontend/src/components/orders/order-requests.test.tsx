import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { formatCurrency } from "@/lib/utils";
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
let requestItems: object[] = request.items;

beforeEach(() => {
  includeRequest = true;
  requestItems = request.items;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/")
      return {
        data: {
          results: includeRequest ? [{ ...request, items: requestItems }] : [],
          count: includeRequest ? 1 : 0,
          next: null,
        },
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
  // Заявка без цен: «0 ₸» выглядел бы бесплатным заказом.
  expect(screen.getByText("Не рассчитана")).toBeInTheDocument();
  expect(screen.getByText("2 меш.")).toBeInTheDocument();
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

it("rereads a request whose items changed while the dialog was open", async () => {
  const user = userEvent.setup();
  const message = "Состав заявки изменился — обновите заявку";
  mocks.post.mockRejectedValue(
    Object.assign(new Error(message), { response: { status: 400, data: { detail: message, code: "invalid_item" } } }),
  );
  render(<Harness />);
  await user.click(await screen.findByRole("button", { name: "Проверить и подтвердить" }));
  const dialog = await screen.findByRole("dialog", { name: /Заказ #621/ });
  await user.selectOptions(within(dialog).getByRole("combobox", { name: "Отдел продаж" }), "main");
  await user.type(within(dialog).getByRole("spinbutton", { name: "Цена: Мука" }), "10");
  // Пока окно было открыто, клиент поменял состав заявки.
  requestItems = [{ id: 9, product: 2, quantity: 3, product_label: "Отруби", client_price: "50" }];
  await user.click(within(dialog).getByRole("button", { name: "Подтвердить заказ" }));
  await user.click(within(dialog).getByRole("button", { name: "Да, закрепить и подтвердить" }));

  expect(await within(dialog).findByText(message)).toBeInTheDocument();
  expect(await within(dialog).findByRole("spinbutton", { name: "Цена: Отруби" })).toHaveValue(50);
  expect(within(dialog).queryByRole("spinbutton", { name: "Цена: Мука" })).not.toBeInTheDocument();
});

it("estimates a request by the client price list", async () => {
  requestItems = [
    { id: 7, product: 1, quantity: 2, product_label: "Мука", client_price: "500" },
    { id: 8, product: 2, quantity: 3, product_label: "Отруби", unit_price: "100" },
  ];
  render(<Harness />);
  const estimate = `≈ ${formatCurrency(1300, "KZT")}`.replace(/\s/g, " ");
  expect(await screen.findByText(estimate)).toBeInTheDocument();
  expect(screen.getByText("5 меш.")).toBeInTheDocument();
});
