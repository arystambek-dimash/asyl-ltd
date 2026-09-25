import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { formatCurrency } from "@/lib/utils";
import { MoneyTrendChart } from "./money-trend-chart";

// В jsdom у графика нет размеров; проверяем доступное описание, а не SVG.
vi.mock("recharts", () => {
  const Stub = () => null;
  return { Area: Stub, AreaChart: Stub, CartesianGrid: Stub, ResponsiveContainer: Stub, Tooltip: Stub, XAxis: Stub };
});

it("describes every day with the shared revenue/received labels", () => {
  render(
    <MoneyTrendChart
      data={[
        { label: "29", revenue: 1200, received: 250 },
        { label: "30", revenue: 0, received: 75 },
      ]}
      currency="KZT"
      className="h-40"
      formatLabel={(label) => `День ${label}`}
    />,
  );

  expect(screen.getByRole("img", { name: "График выручки и поступлений по дням, 2 дней." })).toBeInTheDocument();
  const items = screen.getAllByRole("listitem").map((item) => item.textContent);
  expect(items).toEqual([
    `День 29: выручка ${formatCurrency(1200, "KZT")}, поступило ${formatCurrency(250, "KZT")}`,
    `День 30: выручка ${formatCurrency(0, "KZT")}, поступило ${formatCurrency(75, "KZT")}`,
  ]);
});
