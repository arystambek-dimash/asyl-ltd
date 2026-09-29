import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CameraAnalyticsOverview } from "./camera-analytics-overview";
import type { AlwaysOnDailyCameraAnalytics, AlwaysOnHistoryPoint } from "@/lib/types";

const point: AlwaysOnHistoryPoint = {
  day: "2026-03-01",
  model_total: 817,
  model_per_color: { red: 817 },
  colors: [{ color: "red", total: 817, percent: 100 }],
  adjustment: 0,
  adjustment_per_color: {},
  total: 817,
  updated_at: null,
};
const daily: AlwaysOnDailyCameraAnalytics = {
  ...point,
  camera: "cam3",
  period_total: 817,
  all_time_total: 166947,
  history: [point],
};
function setup(overrides: Partial<React.ComponentProps<typeof CameraAnalyticsOverview>> = {}) {
  const props: React.ComponentProps<typeof CameraAnalyticsOverview> = {
    today: point.day,
    dateFrom: point.day,
    dateTo: point.day,
    onRangeChange: vi.fn(),
    daily,
    loading: false,
    available: true,
    onRetry: vi.fn(),
    isShipping: false,
    receiptMapping: { status: "ready", mappings: [] },
    selectedDay: null,
    onSelectDay: vi.fn(),
    canEdit: false,
    onEditDay: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  return { ...render(<CameraAnalyticsOverview {...props} />), props };
}

describe("camera analytics overview", () => {
  it("marks bags whose colour CRM took from neighbours and keeps unresolved ones apart", () => {
    setup({
      daily: {
        ...daily,
        colors: [
          { color: "red", total: 810, percent: 99, inferred: { neighbors: 4, votes: 1 } },
          { color: "unknown", total: 7, percent: 1 },
        ],
      },
    });
    expect(screen.getByText("по соседям 4 · по голосам 1")).toBeInTheDocument();
    expect(screen.getAllByText("Не определён").length).toBeGreaterThan(0);
    expect(document.querySelectorAll("[data-inferred-badge]")).toHaveLength(1);
  });

  it("offers calendar presets across a month boundary and resets to today", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    expect(screen.getByRole("button", { name: "Сегодня" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Вчера" }));
    expect(props.onRangeChange).toHaveBeenLastCalledWith({ from: "2026-02-28", to: "2026-02-28" });
    await user.click(screen.getByRole("button", { name: "7 дней" }));
    expect(props.onRangeChange).toHaveBeenLastCalledWith({ from: "2026-02-23", to: "2026-03-01" });
    await user.click(screen.getByRole("button", { name: "30 дней" }));
    expect(props.onRangeChange).toHaveBeenLastCalledWith({ from: "2026-01-31", to: "2026-03-01" });
    await user.click(screen.getByRole("button", { name: "Сегодня" }));
    expect(props.onRangeChange).toHaveBeenLastCalledWith(null);
  });

  it("opens a single day directly without a one-bar chart or lifetime metric", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    expect(screen.queryByRole("region", { name: "График по дням" })).not.toBeInTheDocument();
    expect(screen.queryByText(/архив/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/166.?947/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Выпуск по времени: 01.03.2026, 817 мешков" }));
    expect(props.onSelectDay).toHaveBeenCalledWith("2026-03-01");
  });

  it("shows a range chart whose zero days remain selectable", async () => {
    const user = userEvent.setup();
    const { props } = setup({
      dateFrom: "2026-02-28",
      daily: {
        ...daily,
        history: [{ ...point, day: "2026-02-28", total: 0, model_total: 0, colors: [], model_per_color: {} }, point],
      },
    });
    expect(screen.getByRole("region", { name: "График по дням" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Аналитика за 28.02.2026: 0 мешков" }));
    expect(props.onSelectDay).toHaveBeenCalledWith("2026-02-28");
  });

  it("keeps loading, unavailable and genuinely empty results distinct", async () => {
    const user = userEvent.setup();
    const { props, rerender } = setup({ available: false, loading: true, daily: undefined });
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем данные");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/мешки не учтены/)).not.toBeInTheDocument();
    rerender(<CameraAnalyticsOverview {...props} loading={false} error="Нет связи" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Нет связи");
    await user.click(screen.getByRole("button", { name: "Обновить" }));
    expect(props.onRetry).toHaveBeenCalledOnce();
    rerender(
      <CameraAnalyticsOverview
        {...props}
        loading={false}
        available
        daily={{
          ...daily,
          total: 0,
          model_total: 0,
          model_per_color: {},
          period_total: 0,
          colors: [],
          history: [{ ...point, total: 0, model_total: 0, model_per_color: {}, colors: [] }],
        }}
      />,
    );
    expect(screen.getByText("За этот период мешки не учтены")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves recorded activity when a correction makes the net total zero", () => {
    setup({
      daily: {
        ...daily,
        period_total: 0,
        total: 0,
        adjustment: -817,
        history: [{ ...point, total: 0, adjustment: -817 }],
      },
    });
    expect(screen.queryByText("За этот период мешки не учтены")).not.toBeInTheDocument();
    expect(screen.getByText("Итог исправлен вручную: -817 меш. к счёту камеры.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выпуск по времени: 01.03.2026, 0 мешков" })).toBeInTheDocument();
  });

  it("says when only the colours were corrected by hand", () => {
    setup({ daily: { ...daily, history: [{ ...point, adjustment_per_color: { red: -17, white: 17 } }] } });
    expect(screen.getByText("Цвета исправлены вручную.")).toBeInTheDocument();
    expect(screen.queryByText(/Итог исправлен вручную/)).toBeNull();
  });

  it("offers day editing only to a superuser looking at one day", () => {
    const { rerender, props } = setup();
    expect(screen.queryByRole("button", { name: /Изменить/ })).toBeNull();
    rerender(<CameraAnalyticsOverview {...props} canEdit />);
    expect(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" })).toBeInTheDocument();
    rerender(
      <CameraAnalyticsOverview
        {...props}
        canEdit
        dateFrom="2026-02-28"
        daily={{ ...daily, history: [{ ...point, day: "2026-02-28" }, point] }}
      />,
    );
    expect(screen.queryByRole("button", { name: /Изменить/ })).toBeNull();
  });

  it("edits the day's colours in place and closes the editor after saving", async () => {
    const user = userEvent.setup();
    const { props } = setup({ canEdit: true, isShipping: true });
    await user.click(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" }));
    const editor = screen.getByRole("form", { name: "Исправление цветов мешков" });
    expect(screen.queryByRole("button", { name: "Изменить цвета за 01.03.2026" })).toBeNull();
    await user.clear(within(editor).getByRole("textbox", { name: "Мешков: Красный" }));
    await user.type(within(editor).getByRole("textbox", { name: "Мешков: Красный" }), "800");
    await user.type(within(editor).getByRole("textbox", { name: "Мешков: Белый" }), "{Backspace}17");
    expect(editor).toHaveTextContent("Итого: 817 меш.");
    await user.click(within(editor).getByRole("button", { name: "Сохранить" }));
    expect(props.onEditDay).toHaveBeenCalledWith("2026-03-01", { red: 800, white: 17 });
    expect(screen.queryByRole("form", { name: "Исправление цветов мешков" })).toBeNull();
    expect(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" })).toHaveFocus();
  });

  it("keeps typed numbers while the camera catches up or a poll fails", async () => {
    const user = userEvent.setup();
    const { props, rerender } = setup({ canEdit: true, isShipping: true });
    await user.click(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" }));
    await user.clear(screen.getByRole("textbox", { name: "Мешков: Красный" }));
    await user.type(screen.getByRole("textbox", { name: "Мешков: Красный" }), "800");
    rerender(<CameraAnalyticsOverview {...props} canEdit isShipping available={false} />);
    const editor = screen.getByRole("form", { name: "Исправление цветов мешков" });
    expect(within(editor).getByRole("textbox", { name: "Мешков: Красный" })).toHaveValue("800");
    expect(editor).toHaveTextContent("Камера ещё синхронизируется — после сохранения цифры обновятся.");
    rerender(<CameraAnalyticsOverview {...props} canEdit isShipping />);
    expect(screen.getByRole("textbox", { name: "Мешков: Красный" })).toHaveValue("800");
    expect(screen.queryByText(/Камера ещё синхронизируется/)).toBeNull();
  });

  it("moves focus into the editor and back to «Изменить» when it closes", async () => {
    const user = userEvent.setup();
    setup({ canEdit: true });
    await user.click(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" }));
    expect(screen.getByRole("textbox", { name: "Мешков: Красный" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" })).toHaveFocus();
  });

  it("lets a superuser fill a day the camera left empty", async () => {
    const user = userEvent.setup();
    const empty = { ...point, total: 0, model_total: 0, model_per_color: {}, colors: [] };
    setup({ canEdit: true, daily: { ...daily, ...empty, period_total: 0, history: [empty] } });
    expect(screen.getByText("За этот период мешки не учтены")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Изменить цвета за 01.03.2026" }));
    expect(screen.getByRole("form", { name: "Исправление цветов мешков" })).toBeInTheDocument();
    expect(screen.queryByText("За этот период мешки не учтены")).toBeNull();
  });
});
