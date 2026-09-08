import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { OrderConfirmation } from "./order-confirmation";
import type { Department, Order } from "@/lib/types";

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

it("requires an explicit department even when a legacy order contains the default", async () => {
  const user = userEvent.setup();
  const confirm = vi.fn();
  render(<OrderConfirmation order={order} departments={departments} busy={false} onConfirm={confirm} />);
  expect(screen.getByRole("combobox")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
  await user.selectOptions(screen.getByRole("combobox"), "city");
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  expect(confirm).toHaveBeenCalledWith({ department: "city", prices: { "1": "100" } });
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
  await user.selectOptions(screen.getByRole("combobox"), "main");
  await user.clear(screen.getByRole("spinbutton"));
  expect(screen.getByRole("button", { name: "Подтвердить заказ" })).toBeDisabled();
});
