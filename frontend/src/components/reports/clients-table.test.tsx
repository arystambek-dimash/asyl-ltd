import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ReportClientRow } from "@/lib/types";
import { ClientsTable } from "./clients-table";

const rows: ReportClientRow[] = [
  {
    id: 7,
    name: "Гани Таскен",
    orders: 3,
    bags: 17,
    revenue_by_currency: { KZT: "17000.00" },
    paid_amount_by_currency: { KZT: "9000.00" },
    debt_amount_by_currency: { KZT: "8000.00" },
    order_list: [
      {
        id: 117,
        date: "2026-07-28",
        bags: 10,
        total: "10000.00",
        currency: "KZT",
        remaining_amount: "6000.00",
        payment_status: "partial",
      },
      {
        id: 118,
        date: "2026-07-27",
        bags: 5,
        total: "5000.00",
        currency: "KZT",
        remaining_amount: "0.00",
        payment_status: "settled",
      },
      {
        id: 119,
        date: "2026-07-26",
        bags: 2,
        total: "2000.00",
        currency: "KZT",
        remaining_amount: "2000.00",
        payment_status: "unpaid",
      },
    ],
  },
];

describe("ClientsTable", () => {
  it("показывает итоги клиента, а заказы — только после раскрытия", async () => {
    const user = userEvent.setup();
    render(<ClientsTable clients={rows} />);

    expect(screen.getByText("Гани Таскен")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /№117/ })).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /Гани Таскен/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const order = screen.getByRole("link", { name: /№117/ });
    expect(order).toHaveAttribute("href", "/orders/117");
    expect(screen.getByText(/Частично оплачен/)).toBeInTheDocument();
    expect(screen.getByText("Оплачен")).toBeInTheDocument();
    expect(screen.getByText(/В долгу/)).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByRole("link", { name: /№117/ })).not.toBeInTheDocument();
  });

  it("пустой список — заглушка", () => {
    render(<ClientsTable clients={[]} />);
    expect(screen.getByText("Здесь пусто")).toBeInTheDocument();
  });
});
