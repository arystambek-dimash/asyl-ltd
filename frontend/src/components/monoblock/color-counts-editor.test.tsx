import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ColorCountsEditor } from "./color-counts-editor";

vi.mock("@/lib/api", () => ({ apiError: (e: Error) => e.message }));

function setup(overrides: Partial<React.ComponentProps<typeof ColorCountsEditor>> = {}) {
  const props: React.ComponentProps<typeof ColorCountsEditor> = {
    // Синий уже исправлен вручную: камера насчитала 1400, показано 1360.
    items: [
      { color: "blue", total: 1360, percent: 97.1 },
      { color: "unclassified", total: 40, percent: 2.9 },
    ],
    cameraCounts: { blue: 1400, unclassified: 40 },
    total: 1400,
    onSave: vi.fn().mockResolvedValue(undefined),
    onCancel: vi.fn(),
    ...overrides,
  };
  return { ...render(<ColorCountsEditor {...props} />), props };
}

const field = (label: string) => screen.getByRole("textbox", { name: `Мешков: ${label}` });

describe("ColorCountsEditor", () => {
  it("lists shown colours by count, then the standard ones, and «Не определён» last", () => {
    setup();
    expect(screen.getAllByRole("textbox").map((input) => input.getAttribute("aria-label"))).toEqual([
      "Мешков: Синий",
      "Мешков: Красный",
      "Мешков: Белый",
      "Мешков: Зелёный",
      "Мешков: Не определён",
    ]);
    expect(field("Синий")).toHaveValue("1360");
    expect(field("Красный")).toHaveValue("0");
    // Число камеры видно только там, где оно расходится с полем.
    expect(screen.getByText("камера: 1 400")).toBeInTheDocument();
    expect(screen.getAllByText(/^камера:/)).toHaveLength(1);
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 400 меш.");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    // Фокус сразу в первом поле: кнопка «Изменить» исчезла, фокус не должен упасть на <body>.
    expect(field("Синий")).toHaveFocus();
  });

  it("moves the total by the visible difference and sends only changed colours", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.clear(field("Не определён"));
    await user.type(field("Не определён"), "0");
    await user.clear(field("Белый"));
    await user.type(field("Белый"), "40");
    await user.clear(field("Синий"));
    await user.type(field("Синий"), "1360");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 400 меш.");
    await user.clear(field("Белый"));
    await user.type(field("Белый"), "45");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 405 меш.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(props.onSave).toHaveBeenCalledWith({ unclassified: 0, white: 45 });
  });

  it("fills the camera's own counts with «Как у камеры»", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.click(screen.getByRole("button", { name: "Как у камеры" }));
    expect(field("Синий")).toHaveValue("1400");
    expect(screen.queryByRole("button", { name: "Как у камеры" })).toBeNull();
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 440 меш.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(props.onSave).toHaveBeenCalledWith({ blue: 1400 });
  });

  it("«Как у камеры» does not freeze colours the camera keeps counting", async () => {
    const user = userEvent.setup();
    const items = [
      { color: "blue", total: 1360, percent: 99.6 },
      { color: "red", total: 5, percent: 0.4 },
    ];
    const { props, rerender } = setup({ items, cameraCounts: { blue: 1400, red: 5 }, total: 1365 });
    await user.click(screen.getByRole("button", { name: "Как у камеры" }));
    // Опрос принёс ещё 3 красных мешка — они не должны пропасть при сохранении.
    rerender(
      <ColorCountsEditor
        {...props}
        items={[items[0], { color: "red", total: 8, percent: 0.6 }]}
        cameraCounts={{ blue: 1400, red: 8 }}
        total={1368}
      />,
    );
    expect(field("Красный")).toHaveValue("8");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 408 меш.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(props.onSave).toHaveBeenCalledWith({ blue: 1400 });
  });

  it("does not send a colour retyped with the number shown when editing began", async () => {
    const user = userEvent.setup();
    const { props, rerender } = setup();
    await user.clear(field("Синий"));
    rerender(
      <ColorCountsEditor
        {...props}
        items={[{ color: "blue", total: 1365, percent: 97.2 }, props.items[1]]}
        cameraCounts={{ blue: 1405, unclassified: 40 }}
        total={1405}
      />,
    );
    await user.type(field("Синий"), "1360");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    await user.clear(field("Белый"));
    await user.type(field("Белый"), "1");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(props.onSave).toHaveBeenCalledWith({ white: 1 });
  });

  it("does not offer «Как у камеры» when nothing differs from the camera", () => {
    setup({ items: [{ color: "red", total: 5, percent: 100 }], cameraCounts: { red: 5 }, total: 5 });
    expect(screen.queryByRole("button", { name: "Как у камеры" })).toBeNull();
  });

  it.each(["", "-1", "1.5", "abc", "1000001"])("rejects «%s» before saving", async (value) => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.clear(field("Красный"));
    if (value) await user.type(field("Красный"), value);
    expect(field("Красный")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/Укажите целое число мешков от 0 до 1 000 000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    await user.type(field("Синий"), "{Enter}");
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it("keeps a refused save inside the editor with the typed numbers", async () => {
    const user = userEvent.setup();
    const { props } = setup({ onSave: vi.fn().mockRejectedValue(new Error("День перенесён в архив")) });
    await user.clear(field("Красный"));
    await user.type(field("Красный"), "3{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("День перенесён в архив");
    expect(field("Красный")).toHaveValue("3");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(props.onCancel).toHaveBeenCalledOnce();
  });

  it("does not overwrite bags the camera counts while the editor is open", async () => {
    const user = userEvent.setup();
    const { props, rerender } = setup();
    await user.clear(field("Красный"));
    await user.type(field("Красный"), "2");
    rerender(
      <ColorCountsEditor
        {...props}
        items={[{ color: "blue", total: 1365, percent: 97.2 }, props.items[1]]}
        cameraCounts={{ blue: 1405, unclassified: 40 }}
        total={1405}
      />,
    );
    expect(field("Синий")).toHaveValue("1365");
    expect(screen.getByText(/Итого:/)).toHaveTextContent("Итого: 1 407 меш.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(props.onSave).toHaveBeenCalledWith({ red: 2 });
  });

  it("shows the hint", () => {
    setup({ hint: "Итог дня в «Цвета мешков» меняется отдельно." });
    expect(screen.getByText("Итог дня в «Цвета мешков» меняется отдельно.")).toBeInTheDocument();
  });
});
