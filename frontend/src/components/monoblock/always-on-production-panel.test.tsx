import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AlwaysOnProductionPayload } from "@/lib/types";
import { AlwaysOnDayRunLog, AlwaysOnProductionPanel } from "./always-on-production-panel";

const payload: AlwaysOnProductionPayload = {
  camera: "cam1",
  timezone: "Asia/Almaty",
  close_time: "19:00",
  current_business_day: "2026-08-16",
  next_run_at: "2026-08-16T14:00:00Z",
  selected_day: null,
  day_runs: [],
  fully_configured: false,
  available_colors: ["red", "blue"],
  mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
  products: [
    { id: 1, label: "Мука красная · 50 кг", color: "Red", color_label: "Красный", weight_kg: "50.00" },
    { id: 2, label: "Мука синяя · 25 кг", color: "Blue", color_label: "Синий", weight_kg: "25.00" },
    { id: 3, label: "Мука зелёная · 50 кг", color: "Green", color_label: "Зелёный", weight_kg: "50.00" },
  ],
  runs: [
    {
      id: 7,
      camera: "cam1",
      business_day: "2026-08-16",
      color: "red",
      started_at: "2026-08-16T04:30:00Z",
      last_counted_at: "2026-08-16T04:45:00Z",
      ended_at: null,
      model_bags: 126,
      is_approximate: false,
      status: "active",
    },
  ],
  preview: [
    {
      color: "red",
      detected_bags: 126,
      correction_bags: 0,
      net_bags: 126,
      product: 1,
      product_label: "Мука красная · 50 кг",
      configured: true,
    },
  ],
  batches: [],
};

describe("AlwaysOnProductionPanel", () => {
  it("показывает настройки автоприхода без общего журнала цветов", () => {
    render(<AlwaysOnProductionPanel payload={payload} loading={false} error={null} saving={false} onSave={vi.fn()} />);

    expect(screen.queryByText("Когда выпускался каждый цвет")).not.toBeInTheDocument();
    expect(screen.queryByText("идёт сейчас")).not.toBeInTheDocument();
    expect(screen.getByText("126")).toBeInTheDocument();
    expect(screen.getByText("Автоприход на склад")).toBeInTheDocument();
    expect(screen.getByText("19:00")).toBeInTheDocument();
    expect(screen.getByText(/Не настроено:/)).toHaveTextContent("Синий");
  });

  it("предлагает товар совпадающего цвета и сохраняет выбранное сопоставление", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<AlwaysOnProductionPanel payload={payload} loading={false} error={null} saving={false} onSave={onSave} />);

    const blueSelect = screen.getByLabelText("Товар для цвета Синий");
    expect(within(blueSelect).getAllByRole("option")).toHaveLength(2);
    expect(within(blueSelect).queryByRole("option", { name: /зелёная/ })).not.toBeInTheDocument();

    await user.selectOptions(blueSelect, "2");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith([
      { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
      { color: "blue", product: 2, product_label: "Мука синяя · 25 кг" },
    ]);
  });
});

describe("AlwaysOnDayRunLog", () => {
  it("показывает точное время, активный период и приблизительную запись выбранного дня", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="Asia/Almaty"
        loading={false}
        error={null}
        runs={[
          payload.runs[0],
          {
            ...payload.runs[0],
            id: 8,
            color: "blue",
            started_at: "2026-08-16T05:00:00Z",
            last_counted_at: "2026-08-16T05:25:00Z",
            ended_at: "2026-08-16T05:25:00Z",
            model_bags: 41,
            is_approximate: true,
            status: "closed",
          },
        ]}
      />,
    );

    expect(screen.getByText("09:30")).toBeInTheDocument();
    expect(screen.getByText("идёт сейчас")).toBeInTheDocument();
    expect(screen.getByText("10:25")).toBeInTheDocument();
    expect(screen.getByText("≈ приблизительно")).toBeInTheDocument();
    expect(screen.getByText("41")).toBeInTheDocument();
  });

  it("явно показывает пустой выбранный день", () => {
    render(<AlwaysOnDayRunLog day="2026-08-15" timezone="Asia/Almaty" loading={false} error={null} runs={[]} />);

    expect(screen.getByText(/15\.08\.2026/)).toHaveTextContent("Детализация времени");
  });

  it("не приписывает весь объём выбранному дню для сквозного периода", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-17"
        timezone="Asia/Almaty"
        loading={false}
        error={null}
        runs={[
          {
            ...payload.runs[0],
            id: 9,
            started_at: "2026-08-16T18:58:00Z",
            last_counted_at: "2026-08-16T19:02:00Z",
            ended_at: "2026-08-16T19:02:00Z",
            model_bags: 9,
            status: "closed",
            starts_before_day: true,
            ends_after_day: false,
            is_partial_for_day: true,
          },
        ]}
      />,
    );

    expect(screen.getByText("с 00:00")).toBeInTheDocument();
    expect(screen.getByText("сквозной период")).toBeInTheDocument();
    expect(screen.queryByText("9")).not.toBeInTheDocument();
  });
});
