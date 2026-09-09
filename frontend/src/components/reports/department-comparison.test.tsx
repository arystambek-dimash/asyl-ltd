import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { DepartmentComparison } from "./department-comparison";
import type { DepartmentReport } from "@/lib/types";

const rows: DepartmentReport[] = [
  {
    code: "a",
    name: "Мельница",
    color: "blue",
    orders: 3,
    sales_by_currency: { KZT: "300", USD: "1" },
    received_by_currency: { KZT: "100" },
    refunded_by_currency: { KZT: "20" },
    net_by_currency: { KZT: "80" },
  },
  {
    code: "b",
    name: "Город",
    color: "orange",
    orders: 2,
    sales_by_currency: { KZT: "200", USD: "10" },
    received_by_currency: { KZT: "200" },
    refunded_by_currency: {},
    net_by_currency: { KZT: "200" },
  },
];
it("ranks sales within one currency and distinguishes receipts from sales", async () => {
  render(<DepartmentComparison rows={rows} />);
  let first = within(screen.getAllByRole("row")[1]);
  expect(first.getByText("Мельница")).toBeInTheDocument();
  expect(first.getByText("Больше продаж · KZT")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole("combobox"), "USD");
  first = within(screen.getAllByRole("row")[1]);
  expect(first.getByText("Город")).toBeInTheDocument();
  expect(first.getByText("Больше продаж · USD")).toBeInTheDocument();
});
it("cashier comparison ranks net receipts without pretending sales were loaded", () => {
  render(
    <DepartmentComparison rows={rows.map((row) => ({ ...row, sales_by_currency: null, orders: null }))} incomeOnly />,
  );
  expect(screen.queryByRole("columnheader", { name: "Отгружено" })).not.toBeInTheDocument();
  expect(within(screen.getAllByRole("row")[1]).getByText("Город")).toBeInTheDocument();
  expect(screen.getByText("Больше поступлений · KZT")).toBeInTheDocument();
});
