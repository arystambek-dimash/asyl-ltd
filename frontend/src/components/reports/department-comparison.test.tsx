import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DepartmentComparison } from "./department-comparison";
import type { DepartmentReport } from "@/lib/types";

const downloadMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/download", () => ({ downloadBlob: downloadMock }));

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
    payments: 4,
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
    payments: 2,
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

it("выгружает CSV через общий downloadBlob с BOM для Excel", async () => {
  render(<DepartmentComparison rows={rows} from="2026-09-01" to="2026-09-24" />);
  await userEvent.click(screen.getByRole("button", { name: /CSV/ }));

  expect(downloadMock).toHaveBeenCalledWith(expect.any(Blob), "departments-2026-09-01-2026-09-24.csv");
  const blob = downloadMock.mock.calls[0][0] as Blob;
  // text() срезает BOM при декодировании — проверяем байты.
  expect([...new Uint8Array(await blob.arrayBuffer()).slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const csv = await blob.text();
  expect(csv).toContain('"Мельница";"KZT";"300";"100";"20";"80"');
});
