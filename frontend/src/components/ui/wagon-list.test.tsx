import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WagonList } from "./wagon-list";

const wagons = [
  { number: "28087658", product_label: "Мука · 50 кг", bags: 1360, weight_kg: "68000.00" },
  { number: "28087666", product_label: "Мука · 50 кг", bags: 1350, weight_kg: "67500.00" },
];

describe("WagonList", () => {
  it("показывает вагоны и станцию, номера — чипами", () => {
    render(<WagonList wagons={wagons} station="Раустан" />);

    expect(screen.getByText("2 вагона · ст. Раустан")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Вагоны" });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["28087658", "28087666"]);
    expect(within(list).getByText("28087666")).toHaveAttribute("title", "Мука · 50 кг · 1350 меш. · 67,5 т");
  });

  it("таблицей — с товаром, мешками и тоннами каждого вагона", () => {
    render(<WagonList wagons={wagons} variant="table" />);

    const rows = within(screen.getByRole("table", { name: "Вагоны" })).getAllByRole("row");
    expect(rows[2]).toHaveTextContent("28087666Мука · 50 кг135067,5");
  });

  it("без заголовка — только номера; без вагонов — ничего", () => {
    const { container, rerender } = render(<WagonList wagons={wagons} headline={false} />);
    expect(screen.queryByText(/2 вагона/)).not.toBeInTheDocument();

    rerender(<WagonList wagons={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
