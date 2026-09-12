import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { DonutChart } from "./donut-chart";

const segments = [
  { key: "cash", label: "Наличные", value: 300, color: "green" },
  { key: "kaspi", label: "QR", value: 100, color: "blue" },
  { key: "card", label: "Карта", value: 0, color: "gray" },
];

it("draws one arc per positive segment and describes the shares", () => {
  const { container } = render(<DonutChart segments={segments} centerValue="400 ₸" centerLabel="2 оплаты" />);
  const arcs = container.querySelectorAll("circle[stroke-dasharray]");
  expect(arcs).toHaveLength(2);
  expect(arcs[0]).toHaveAttribute("stroke-dasharray", "75 25");
  expect(arcs[1]).toHaveAttribute("stroke-dashoffset", "-75");
  expect(screen.getByRole("img", { name: "400 ₸, 2 оплаты. Наличные: 75%, QR: 25%" })).toBeInTheDocument();
  expect(screen.getByText("400 ₸")).toBeInTheDocument();
});

it("shows only the track when there is nothing to split", () => {
  const { container } = render(<DonutChart segments={[]} centerValue="0 ₸" emptyLabel="Пусто" />);
  expect(container.querySelectorAll("circle")).toHaveLength(1);
  expect(screen.getByRole("img", { name: "0 ₸. Пусто" })).toBeInTheDocument();
});
