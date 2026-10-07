import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { OrderProductOption } from "@/components/orders/order-form-parts";
import { ProductPicker } from "./product-picker";

function option(id: number, name: string, weight: string, bags: number, extra: Partial<OrderProductOption> = {}) {
  return {
    id,
    name,
    label: `${name} · Синий ${Number(weight)} кг`,
    weight_kg: weight,
    codes: [],
    color_label: "Синий",
    available_bags: bags,
    stock_by_warehouse: { "1": bags },
    ...extra,
  } as OrderProductOption;
}

const PRODUCTS = [
  option(1, "Первый сорт DIKHAN BABA NAN 25КГ", "25.00", 99332, { codes: ["Д1с25"] }),
  option(2, "Высший сорт DIKHAN BABA NAN 50кг", "50.00", 1012724),
  option(3, "Первый сорт DIKHAN BABA NAN 50кг", "50.00", 756387, { codes: ["Д1c"] }),
  option(4, "Высший сорт OMAD 50 кг", "50.00", 0),
  option(5, "Первый сорт Алтын Тәжі 50кг", "50.00", 9897296),
  option(6, "Высший сорт KOROL 50кг", "50.00", 478942),
];

/** Текст строк списка; разряды мешков — неразрывными пробелами, как во всей CRM. */
const optionTexts = () => screen.getAllByRole("option").map((row) => row.textContent?.replace(/\s/g, " "));

function Harness({
  products = PRODUCTS,
  onChange = vi.fn(),
  initial = "",
}: {
  products?: OrderProductOption[];
  onChange?: (id: string) => void;
  initial?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    <div className="grid">
      <ProductPicker
        products={products}
        value={value}
        onChange={(id) => {
          setValue(id);
          onChange(id);
        }}
        bagsOf={(product) => product.available_bags ?? 0}
        allowOutOfStock={false}
        ariaLabel="Товар, позиция 1"
      />
    </div>
  );
}

const trigger = () => screen.getByRole("button", { name: /^Товар, позиция 1/ });

describe("ProductPicker", () => {
  it("shows the brand on every row and as filter buttons, without colours", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());

    expect(screen.getAllByRole("radio").map((chip) => chip.textContent)).toEqual([
      "Все6",
      "DIKHAN BABA NAN3",
      // Равные по числу товаров марки — по алфавиту (русская сортировка: кириллица раньше).
      "Алтын Тәжі1",
      "KOROL1",
      "OMAD1",
    ]);
    expect(optionTexts().slice(0, 3)).toEqual([
      "DIKHAN BABA NANВысший сорт50 кг1 012 724 меш.",
      "DIKHAN BABA NANПервый сорт50 кг756 387 меш.",
      "DIKHAN BABA NANПервый сорт25 кг99 332 меш.",
    ]);
    expect(screen.getByRole("listbox", { name: "Товары" })).not.toHaveTextContent("Синий");
  });

  it("narrows the list to one brand by its button", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());
    await user.click(screen.getByRole("radio", { name: /^KOROL/ }));

    expect(optionTexts()).toEqual(["KOROLВысший сорт50 кг478 942 меш."]);
  });

  it("finds a product by its report code, Cyrillic or Latin «с», and shows brand and grade on the button", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);

    await user.click(trigger());
    // Код записан «Д1c» с латинской c — ищем кириллицей.
    await user.type(screen.getByLabelText("Поиск товара"), "д1с");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    await user.click(screen.getByRole("option", { name: /Первый сорт50 кг756/ }));

    expect(onChange).toHaveBeenCalledWith("3");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger()).toHaveAccessibleName("Товар, позиция 1: DIKHAN BABA NAN · Первый сорт · 50 кг");
    expect(trigger()).toHaveTextContent("DIKHAN BABA NANПервый сорт50 кг");
    expect(trigger()).toHaveFocus();
  });

  it("matches every typed word on its own: grade and pack size", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());
    await user.type(screen.getByLabelText("Поиск товара"), "первый 50");

    expect(optionTexts()).toEqual([
      "DIKHAN BABA NANПервый сорт50 кг756 387 меш.",
      "Алтын ТәжіПервый сорт50 кг9 897 296 меш.",
    ]);
  });

  it("picks the first available match on Enter instead of submitting the order form", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    const onChange = vi.fn();
    render(
      <form onSubmit={onSubmit}>
        <Harness onChange={onChange} />
        <button type="submit">Создать заказ</button>
      </form>,
    );

    await user.click(trigger());
    await user.type(screen.getByLabelText("Поиск товара"), "высший{Enter}");

    expect(onSubmit).not.toHaveBeenCalled();
    // OMAD без остатка — первым доступным идёт DIKHAN высший.
    expect(onChange).toHaveBeenCalledWith("2");
  });

  it("keeps the chosen product on Enter with an empty search", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} initial="6" />);

    await user.click(trigger());
    await user.click(screen.getByLabelText("Поиск товара"));
    await user.keyboard("{Enter}");

    expect(onChange).not.toHaveBeenCalled();
  });

  it("opens with all brands again after a brand filter was used", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());
    await user.click(screen.getByRole("radio", { name: /^KOROL/ }));
    await user.click(trigger());
    await user.click(trigger());

    expect(screen.getByRole("radio", { name: /^Все/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByRole("option")).toHaveLength(PRODUCTS.length);
  });

  it("treats spelling variants of one brand as one, with the grade anywhere in the name", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        products={[
          option(10, "Высший сорт KOROL 50кг", "50.00", 10),
          option(11, "korol  первый сорт 25кг", "25.00", 5),
        ]}
      />,
    );

    await user.click(trigger());

    // Одна марка — кнопки фильтра не нужны; обе строки под KOROL.
    expect(screen.queryByRole("radiogroup", { name: "Марка" })).not.toBeInTheDocument();
    expect(optionTexts()).toEqual(["KOROLВысший сорт50 кг10 меш.", "korolПервый сорт25 кг5 меш."]);
  });

  it("closes on the already chosen product without touching the row", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} initial="3" />);

    await user.click(trigger());
    await user.click(screen.getByRole("option", { selected: true }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes only the list on Escape, not the order dialog around it", async () => {
    const user = userEvent.setup();
    const dialogEscape = vi.fn();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && dialogEscape();
    document.addEventListener("keydown", onKey);
    render(<Harness />);

    await user.click(trigger());
    fireEvent.keyDown(screen.getByLabelText("Поиск товара"), { key: "Escape" });

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(dialogEscape).not.toHaveBeenCalled();
    expect(trigger()).toHaveFocus();
    document.removeEventListener("keydown", onKey);
  });

  it("does not let an out-of-stock product be chosen", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(trigger());
    const omad = screen.getAllByRole("option").find((row) => row.textContent?.startsWith("OMAD"));

    expect(omad).toBeDisabled();
    expect(omad).toHaveTextContent("нет в наличии");
  });

  it("names the colour only when two products would otherwise look the same", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        products={[
          option(7, "Высший сорт KOROL 50кг", "50.00", 10, { color_label: "Синий" }),
          option(8, "Высший сорт KOROL 50кг", "50.00", 5, { color_label: "Красный" }),
          option(9, "Второй сорт KOROL 50кг", "50.00", 3, { color_label: "Зелёный" }),
        ]}
      />,
    );

    await user.click(trigger());

    expect(optionTexts()).toEqual([
      "KOROL · СинийВысший сорт50 кг10 меш.",
      "KOROL · КрасныйВысший сорт50 кг5 меш.",
      "KOROLВторой сорт50 кг3 меш.",
    ]);
  });
});
