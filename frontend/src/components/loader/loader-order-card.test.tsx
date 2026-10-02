import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { colorMeta } from "@/lib/monoblock-colors";
import { makeLoaderItem, makeLoaderOrder } from "@/test-utils/factories";
import { LoaderOrderCard } from "./loader-order-card";

const CLIENT = "ТОО Длинное название клиента из Шымкента";
const FIRST_FLOUR = "Первый сорт DIKHAN BABA NAN 50кг · Красный 50 кг";

/** Заказ из жалобы грузчиков: длинное название первой муки и вторая мука — 5 и 12 мешков. */
const twoFlours = makeLoaderOrder(943, {
  client_name: CLIENT,
  truck_number: "403BJN13",
  items: [
    makeLoaderItem({ label: FIRST_FLOUR, quantity: 5 }),
    makeLoaderItem({ label: "Высший сорт · Зелёный 50 кг", quantity: 12, color: "Green" }),
  ],
  bags: 17,
  total_kg: "850.00",
});

/** a стоит в разметке раньше b. */
const before = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

const itemRows = () => within(screen.getByRole("list", { name: "Товары" })).getAllByRole("listitem");

describe("карточка заказа у грузчика", () => {
  it("первым — клиент, под ним номер заказа и транспорт", () => {
    render(<LoaderOrderCard order={twoFlours} onOpen={vi.fn()} />);

    // Имя клиента начинает кнопку: грузчик ищет заказ по нему.
    expect(screen.getByRole("button", { name: new RegExp(`^${CLIENT}.*№943`) })).toBeInTheDocument();
    expect(before(screen.getByText(CLIENT), itemRows()[0])).toBe(true);
  });

  it("каждый товар — своей строкой: точка цвета мешка, полное название и мешки", () => {
    render(<LoaderOrderCard order={twoFlours} onOpen={vi.fn()} />);
    const rows = itemRows();

    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(FIRST_FLOUR);
    expect(within(rows[0]).getByText("5 мешков")).toBeInTheDocument();
    expect(rows[0].querySelector("[aria-hidden]")).toHaveClass(colorMeta("Red").dot);
    expect(rows[1]).toHaveTextContent("Высший сорт · Зелёный 50 кг");
    expect(within(rows[1]).getByText("12 мешков")).toBeInTheDocument();
    expect(rows[1].querySelector("[aria-hidden]")).toHaveClass(colorMeta("Green").dot);
  });

  it("ни имя, ни товары не обрезаются", () => {
    const { container } = render(<LoaderOrderCard order={twoFlours} onOpen={vi.fn()} />);

    expect(container.querySelector(".truncate")).toBeNull();
  });

  it("под товарами — итог: мешки и вес; у вагона вес в тоннах", () => {
    const { rerender } = render(<LoaderOrderCard order={twoFlours} onOpen={vi.fn()} />);
    expect(screen.getByText("Итого 17 мешков · 850 кг")).toBeInTheDocument();

    rerender(
      <LoaderOrderCard
        order={makeLoaderOrder(625, {
          transport_type: "train",
          items: [makeLoaderItem({ quantity: 1360 })],
          bags: 1360,
          total_kg: "68000.00",
        })}
      />,
    );
    expect(screen.getByText("Итого 1360 мешков · 68 т")).toBeInTheDocument();
  });
});
