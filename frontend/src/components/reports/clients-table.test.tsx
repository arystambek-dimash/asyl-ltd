import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ReportClientRow } from "@/lib/types";
import { ClientsTable } from "./clients-table";

const rows: ReportClientRow[] = [
  {
    id: 7,
    name: "Гани Таскен",
    orders: 2,
    bags: 15,
    revenue_by_currency: { KZT: "15000.00" },
    debt_amount_by_currency: { KZT: "10000.00" },
    order_list: [
      { id: 117, date: "2026-07-28", bags: 10, total: "10000.00", currency: "KZT", on_debt: true },
      { id: 118, date: "2026-07-27", bags: 5, total: "5000.00", currency: "KZT", on_debt: false },
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
    // «В долг» есть и в шапке таблицы, поэтому бейдж — это второе вхождение.
    expect(screen.getAllByText("В долг")).toHaveLength(2);
    expect(screen.getByText("Оплачен")).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByRole("link", { name: /№117/ })).not.toBeInTheDocument();
  });

  it("пустой список — заглушка", () => {
    render(<ClientsTable clients={[]} />);
    expect(screen.getByText("Здесь пусто")).toBeInTheDocument();
  });
});
