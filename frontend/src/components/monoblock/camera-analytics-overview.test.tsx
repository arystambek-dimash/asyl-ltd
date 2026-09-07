import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CameraAnalyticsOverview } from "./camera-analytics-overview";
import type { AlwaysOnDailyCameraAnalytics, AlwaysOnHistoryPoint } from "@/lib/types";

const point: AlwaysOnHistoryPoint = {
  day: "2026-03-01",
  model_total: 817,
  model_per_color: { red: 817 },
  model_per_brand: {},
  colors: [{ color: "red", total: 817, percent: 100 }],
  brands: [],
  adjustment: 0,
  total: 817,
  updated_at: null,
};
const daily: AlwaysOnDailyCameraAnalytics = {
  ...point,
  camera: "cam3",
  period_total: 817,
  all_time_total: 166947,
  history: [point],
  dominant_color: "red",
  dominant_brand: null,
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
    ...overrides,
  };
  return { ...render(<CameraAnalyticsOverview {...props} />), props };
}

describe("camera analytics overview", () => {
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
    expect(screen.getByText(/Итог учитывает корректировку -817 меш/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выпуск по времени: 01.03.2026, 0 мешков" })).toBeInTheDocument();
  });
});
