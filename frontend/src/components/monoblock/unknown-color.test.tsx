import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ apiError: (error: Error) => error.message }));

import type { AlwaysOnProductMapping } from "@/lib/types";
import { InferredBadge, inferredLabel, UnknownColorDialog, unresolvedBagsLabel } from "./unknown-color";

const mappings: AlwaysOnProductMapping[] = [
  { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
  { color: "blue", product: null, product_label: null },
  { color: "green", product: 3, product_label: "Мука зелёная · 50 кг" },
  { color: "unknown", product: 9, product_label: "Старая привязка" },
];

function renderDialog(overrides: Partial<Parameters<typeof UnknownColorDialog>[0]> = {}) {
  const props = {
    open: true,
    businessDay: "2026-08-25",
    pendingBags: 3,
    posted: false,
    mappings,
    onClose: vi.fn(),
    onSubmit: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(<UnknownColorDialog {...props} />);
  return props;
}

describe("InferredBadge", () => {
  it("подписывает способ и число мешков", () => {
    expect(inferredLabel({ neighbors: 2 })).toBe("по соседям · 2");
    expect(inferredLabel({ votes: 1, neighbors: 3 })).toBe("по соседям 3 · по голосам 1");
    expect(inferredLabel({ manual: 1 })).toBe("вручную · 1");
    expect(inferredLabel({})).toBe("");
    expect(inferredLabel(undefined)).toBe("");
  });

  it("не рисуется, когда всё распознала камера", () => {
    const { container } = render(<InferredBadge inferred={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("объясняет пометку во всплывающей подсказке", () => {
    render(<InferredBadge inferred={{ neighbors: 2 }} />);
    const badge = screen.getByText("по соседям · 2");
    expect(badge).toHaveAttribute("title", expect.stringContaining("соседн"));
  });
});

describe("unresolvedBagsLabel", () => {
  it("склоняет число мешков", () => {
    expect(unresolvedBagsLabel(1)).toBe("Цвет не определён: 1 мешок");
    expect(unresolvedBagsLabel(3)).toBe("Цвет не определён: 3 мешка");
    expect(unresolvedBagsLabel(12)).toBe("Цвет не определён: 12 мешков");
  });
});

describe("UnknownColorDialog", () => {
  it("предлагает только цвета с привязанным товаром", async () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText("Цвет мешков");
    const options = within(select)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toEqual(["Красный → Мука красная · 50 кг", "Зелёный → Мука зелёная · 50 кг"]);
    expect(within(dialog).getByLabelText("Мешков")).toHaveValue(3);
    expect(within(dialog).getByText(/войдут в приход смены/)).toBeInTheDocument();
  });

  it("отправляет цвет, количество и причину", async () => {
    const user = userEvent.setup();
    const props = renderDialog();
    const dialog = screen.getByRole("dialog");

    const submit = within(dialog).getByRole("button", { name: "Указать цвет" });
    expect(submit).toBeDisabled();
    await user.selectOptions(within(dialog).getByLabelText("Цвет мешков"), "green");
    await user.clear(within(dialog).getByLabelText("Мешков"));
    await user.type(within(dialog).getByLabelText("Мешков"), "2");
    await user.type(within(dialog).getByLabelText("Причина"), "Проверено по записи");
    await user.click(submit);

    expect(props.onSubmit).toHaveBeenCalledWith({
      business_day: "2026-08-25",
      color: "green",
      bags: 2,
      reason: "Проверено по записи",
    });
    expect(props.onClose).toHaveBeenCalled();
  });

  it("не даёт указать больше мешков, чем осталось без цвета", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = screen.getByRole("dialog");
    await user.clear(within(dialog).getByLabelText("Мешков"));
    await user.type(within(dialog).getByLabelText("Мешков"), "4");
    await user.type(within(dialog).getByLabelText("Причина"), "Проверено по записи");

    expect(within(dialog).getByRole("button", { name: "Указать цвет" })).toBeDisabled();
    expect(within(dialog).getByText("Не больше 3")).toBeInTheDocument();
  });

  it("показывает ошибку сервера внутри окна и не закрывает его", async () => {
    const user = userEvent.setup();
    const props = renderDialog({
      posted: true,
      onSubmit: vi.fn().mockRejectedValue(new Error("Без цвета осталось только 1 меш.")),
    });
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/отдельным приходом/)).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Причина"), "Проверено по записи");
    await user.click(within(dialog).getByRole("button", { name: "Указать цвет" }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent("Без цвета осталось только 1 меш.");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("без привязанных товаров объясняет, что сначала нужна настройка", () => {
    renderDialog({ mappings: [{ color: "blue", product: null, product_label: null }] });
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Куда приходовать/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Указать цвет" })).toBeDisabled();
  });
});
