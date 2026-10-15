import { describe, expect, it } from "vitest";
import { productChoices } from "./product-parts";
import type { Product } from "./types";

function product(id: number, name: string, weight_kg = "50", color_label = "Красный"): Product {
  return { id, name, weight_kg, color_label, is_active: true, label: `${name} (${color_label})` };
}

describe("productChoices", () => {
  it("orders like the warehouse list and adds colour only to twins", () => {
    const choices = productChoices([
      product(1, "Первый сорт KOROL 50кг"),
      product(2, "DIKHAN высший сорт 25кг", "25"),
      product(3, "Высший сорт KOROL 50кг", "50", "Красный"),
      product(4, "Высший сорт KOROL 50кг", "50", "Зелёный"),
      product(5, "Высший сорт KOROL 25кг", "25"),
    ]);

    expect(choices).toEqual([
      { id: 3, title: "KOROL · Высший сорт · 50 кг · Красный" },
      { id: 4, title: "KOROL · Высший сорт · 50 кг · Зелёный" },
      { id: 5, title: "KOROL · Высший сорт · 25 кг" },
      { id: 1, title: "KOROL · Первый сорт · 50 кг" },
      { id: 2, title: "DIKHAN · Высший сорт · 25 кг" },
    ]);
  });

  it("falls back to the label when the name is empty", () => {
    expect(productChoices([{ ...product(7, "", "40"), label: "Отруби 40кг" }])).toEqual([
      { id: 7, title: "Отруби · 40 кг" },
    ]);
  });
});
