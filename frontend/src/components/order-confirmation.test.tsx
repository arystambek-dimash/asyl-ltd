import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { OrderConfirmation, type ConfirmContext } from "./order-confirmation";
import type { Department, Me, Order } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

/** Сумма так, как её видит getByText: неразрывные пробелы нормализуются. */
const money = (value: number) => formatCurrency(value, "KZT").replace(/\s/g, " ");

function stock(context: Partial<ConfirmContext> = {}) {
  mocks.get.mockResolvedValue({ data: { items: {}, transport_locked: false, client_country: "", ...context } });
}

beforeEach(() => {
  mocks.get.mockReset();
  stock();
});

const order = {
  id: 12,
  department: "main",
  currency: "KZT",
  items: [{ id: 1, product: 1, product_label: "Мука", quantity: 2, unit_price: "100" }],
} as Order;
const departments = [
  { code: "main", name: "Мельница", is_active: true },
  { code: "city", name: "Город", is_active: true },
] as Department[];

it("uses the assigned client department and cannot redirect a sale to another", async () => {
  const confirm = vi.fn();
  render(
    <OrderConfirmation
      order={{ ...order, client_department: "city", client_department_name: "Город" }}
      departments={departments}
      busy={false}
      onConfirm={confirm}
    />,
  );
  expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toHaveValue("city");
  expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toBeDisabled();
  expect(screen.queryByRole("option", { name: "Мельница" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  expect(confirm).toHaveBeenCalledWith({ department: "city", prices: { "1": "100" } });
});

it("requires an explicit department even when a legacy order contains the default", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  render(<OrderConfirmation order={order} departments={departments} busy={false} onConfirm={confirm} />);
  expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
  await user.selectOptions(screen.getByRole("combobox", { name: "Отдел продаж" }), "city");
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByRole("alertdialog", { name: "Закрепить клиента за отделом «Город»?" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Да, закрепить и подтвердить" }));
  expect(confirm).toHaveBeenCalledWith({ department: "city", prices: { "1": "100" } });
});

it("offers a department employee only their own department and asks before assigning the client", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  useAuth.setState({ me: { sales_department: { id: 2, code: "city", name: "Город", color: "#000" } } as Me });
  try {
    render(<OrderConfirmation order={order} departments={departments} busy={false} onConfirm={confirm} />);

    expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toHaveValue("city");
    expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toBeDisabled();
    expect(screen.queryByRole("option", { name: "Мельница" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
    await user.click(screen.getByRole("button", { name: "Нет" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
    await user.click(screen.getByRole("button", { name: "Да, закрепить и подтвердить" }));
    expect(confirm).toHaveBeenCalledWith({ department: "city", prices: { "1": "100" } });
  } finally {
    useAuth.setState({ me: null });
  }
});

it("requires positive prices and hides inactive department choices", async () => {
  const user = userEvent.setup();
  render(
    <OrderConfirmation
      order={order}
      departments={[...departments, { code: "old", name: "Архивный", is_active: false } as Department]}
      busy={false}
      onConfirm={vi.fn()}
    />,
  );
  expect(screen.queryByRole("option", { name: "Архивный" })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", { name: "Отдел продаж" }), "main");
  await user.clear(screen.getByRole("spinbutton", { name: "Цена: Мука" }));
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
});

const request = {
  ...order,
  client_department: "main",
  client_department_name: "Мельница",
  transport_type: "truck",
  truck_number: "",
  trailer_number: "",
  items: [
    { id: 1, product: 1, product_label: "Мука 1с", quantity: 10, unit_price: "100" },
    { id: 2, product: 2, product_label: "Отруби", quantity: 4, unit_price: null, client_price: "50" },
  ],
} as Order;

it("loads stock on open and gives out what is on hand", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  stock({
    items: { "1": { on_hand: 8, awaiting_shipment: 5 }, "2": { on_hand: 0, awaiting_shipment: 0 } },
  });
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={confirm} />);

  expect(mocks.get).toHaveBeenCalledWith("/orders/12/confirm-context/", expect.anything());
  expect(await screen.findByText("На складе 8 · ждут отгрузки 5")).toBeInTheDocument();
  expect(screen.getByText(`Итого: 14 меш. · ${money(1200)}`)).toBeInTheDocument();
  // Мешки отруби уже обещаны: подтвердить можно, но окно предупреждает.
  expect(screen.getAllByRole("status").map((row) => row.textContent)).toEqual([
    "Свободно 3 меш. — может не хватить",
    "Свободно 0 меш. — может не хватить",
  ]);
  const [flourGive, branGive] = screen.getAllByRole("button", { name: "Отдать сколько есть" });
  expect(branGive).toBeDisabled();

  await user.click(flourGive);

  expect(screen.getByRole("spinbutton", { name: "Количество: Мука 1с" })).toHaveValue(8);
  expect(screen.getByText(`Итого: 12 из 14 меш. · ${money(1000)}`)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Подтвердить 12 из 14 меш." }));
  expect(confirm).toHaveBeenCalledWith({
    department: "main",
    prices: { "1": "100", "2": "50" },
    quantities: { "1": 8 },
  });
});

it("keeps the quantity between one bag and the requested amount", async () => {
  const user = userEvent.setup();
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={vi.fn()} />);
  const quantity = screen.getByRole("spinbutton", { name: "Количество: Мука 1с" });
  expect(quantity).toHaveAttribute("min", "1");
  expect(quantity).toHaveAttribute("max", "10");

  await user.clear(quantity);
  await user.type(quantity, "11");
  expect(screen.getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
  await user.clear(quantity);
  await user.type(quantity, "0");
  expect(screen.getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
  // Окно открывается на весь экран телефона: 16px, чтобы iOS не зумил страницу.
  expect(quantity).toHaveClass("text-base");
  expect(screen.getByRole("spinbutton", { name: "Цена: Мука 1с" })).toHaveClass("text-base");
});

it("sends a truck and trailer typed in the transport block", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={confirm} />);

  const transport = screen.getByRole("group", { name: "Транспорт (можно позже)" });
  await user.type(within(transport).getByLabelText("Тягач"), "07 kg 695 adt");
  await user.type(within(transport).getByLabelText("Прицеп"), "07kg837pb");
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));

  expect(confirm).toHaveBeenCalledWith({
    department: "main",
    prices: { "1": "100", "2": "50" },
    truck_number: "07KG695ADT",
    trailer_number: "07KG837PB",
  });
});

it("says which number the API would refuse and does not block on an empty one", async () => {
  const user = userEvent.setup();
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={vi.fn()} />);
  const trailer = screen.getByLabelText("Прицеп");

  await user.type(trailer, "12");
  await user.tab();

  expect(trailer).toHaveAttribute("aria-invalid", "true");
  expect(trailer).toHaveAccessibleDescription("Номер: от 4 до 12 латинских букв и цифр");
  expect(screen.getByLabelText("Тягач")).not.toHaveAttribute("aria-invalid");
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
  expect(screen.getByText("Исправьте номер или оставьте поле пустым.")).toBeInTheDocument();

  await user.clear(trailer);
  expect(trailer).not.toHaveAttribute("aria-invalid");
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeEnabled();
});

it("shows only the hard error for a truck number that is too short", async () => {
  const user = userEvent.setup();
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={vi.fn()} />);
  const truck = screen.getByLabelText("Тягач");

  await user.type(truck, "12");
  await user.tab();

  expect(truck).toHaveAccessibleDescription("Номер: от 4 до 12 латинских букв и цифр");
  expect(screen.queryByText(/не похож/)).not.toBeInTheDocument();
});

it("takes an eight-digit wagon number", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  render(
    <OrderConfirmation
      order={{ ...request, transport_type: "train" }}
      departments={departments}
      busy={false}
      onConfirm={confirm}
    />,
  );
  expect(screen.queryByLabelText("Прицеп")).not.toBeInTheDocument();
  const wagon = screen.getByLabelText("Номер вагона");
  expect(wagon).toHaveClass("text-base");
  await user.type(wagon, "1234");
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
  expect(wagon).toHaveAccessibleDescription("Номер вагона: 8 цифр");
  await user.type(wagon, "5678");
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  expect(confirm).toHaveBeenCalledWith({
    department: "main",
    prices: { "1": "100", "2": "50" },
    truck_number: "12345678",
  });
});

it("shows a number entered by the client read-only", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  stock({ transport_locked: true });
  render(
    <OrderConfirmation
      order={{ ...request, truck_number: "403BJN13" }}
      departments={departments}
      busy={false}
      onConfirm={confirm}
    />,
  );
  expect(await screen.findByText("403 BJN 13")).toBeInTheDocument();
  expect(screen.getByText("Номер указал клиент — изменить его может только он.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  expect(confirm).toHaveBeenCalledWith({ department: "main", prices: { "1": "100", "2": "50" } });
});

it("shows the total as not calculated while a price is missing", async () => {
  const user = userEvent.setup();
  render(<OrderConfirmation order={request} departments={departments} busy={false} onConfirm={vi.fn()} />);
  await user.clear(screen.getByRole("spinbutton", { name: "Цена: Отруби" }));
  expect(screen.getByText("Итого: 14 меш. · Не рассчитана")).toBeInTheDocument();
});
