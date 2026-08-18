import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AlwaysOnProductionPayload, AlwaysOnProductionRun } from "@/lib/types";
import { AlwaysOnDayRunLog, AlwaysOnProductionPanel, smoothColorRuns } from "./always-on-production-panel";

function makeRun(overrides: Partial<AlwaysOnProductionRun>): AlwaysOnProductionRun {
  return {
    id: 1,
    camera: "cam1",
    business_day: "2026-08-16",
    color: "red",
    started_at: "2026-08-16T04:00:00Z",
    last_counted_at: "2026-08-16T04:10:00Z",
    ended_at: "2026-08-16T04:10:00Z",
    model_bags: 100,
    is_approximate: false,
    status: "closed",
    ...overrides,
  };
}

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
    render(
      <AlwaysOnProductionPanel
        payload={payload}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

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
    render(
      <AlwaysOnProductionPanel
        payload={payload}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={onSave}
      />,
    );

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

  it("оставляет настройки и повторный приход только для чтения без права управления", () => {
    const onRetry = vi.fn();
    render(
      <AlwaysOnProductionPanel
        payload={{
          ...payload,
          batches: [
            {
              id: 9,
              camera: "cam1",
              business_day: "2026-08-15",
              scheduled_for: "2026-08-15T14:00:00Z",
              status: "failed",
              total_bags: 20,
              last_error: "Склад временно недоступен",
              attempts: 1,
              posted_at: null,
              items: [],
            },
          ],
        }}
        loading={false}
        error={null}
        saving={false}
        canManage={false}
        onSave={vi.fn()}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByLabelText("Товар для цвета Красный")).toBeDisabled();
    expect(screen.getByLabelText("Товар для цвета Синий")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить сейчас" })).not.toBeInTheDocument();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("разрешает повторить ошибочный приход с правом управления", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const batch = {
      id: 9,
      camera: "cam1",
      business_day: "2026-08-15",
      scheduled_for: "2026-08-15T14:00:00Z",
      status: "failed" as const,
      total_bags: 20,
      last_error: "Склад временно недоступен",
      attempts: 1,
      posted_at: null,
      items: [],
    };
    render(
      <AlwaysOnProductionPanel
        payload={{ ...payload, batches: [batch] }}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
        onRetry={onRetry}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Повторить сейчас" }));
    expect(onRetry).toHaveBeenCalledWith(batch);
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

  it("переключается между поправленным и сырым видом", async () => {
    const user = userEvent.setup();
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="Asia/Almaty"
        loading={false}
        error={null}
        runs={[
          makeRun({ id: 1, color: "red", model_bags: 2460 }),
          makeRun({ id: 2, color: "blue", model_bags: 3 }),
          makeRun({ id: 3, color: "red", model_bags: 200 }),
        ]}
      />,
    );

    // По умолчанию — поправленный: синее вкрапление (3 меш.) склеено с красным.
    expect(screen.getByRole("button", { name: "Поправленный" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("склеено вкраплений: 1")).toBeInTheDocument();
    expect(screen.getByText("2463")).toBeInTheDocument();
    expect(screen.queryByText("2460")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Сырой" }));

    // В сыром виде исходные три периода видны раздельно, склейки нет.
    expect(screen.getByText("2460")).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getAllByText("меш.")).toHaveLength(3);
    expect(screen.queryByText("2463")).not.toBeInTheDocument();
    expect(screen.queryByText("склеено вкраплений: 1")).not.toBeInTheDocument();
  });

  it("не показывает переключатель, когда склеивать нечего", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="Asia/Almaty"
        loading={false}
        error={null}
        runs={[makeRun({ id: 1, color: "red", model_bags: 2460 })]}
      />,
    );

    expect(screen.queryByRole("button", { name: "Поправленный" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сырой" })).not.toBeInTheDocument();
  });
});

describe("smoothColorRuns", () => {
  it("склеивает мелкое вкрапление другого цвета в предыдущий период и тянет время", () => {
    const result = smoothColorRuns([
      makeRun({
        id: 1,
        color: "red",
        model_bags: 2460,
        ended_at: "2026-08-16T06:49:00Z",
        last_counted_at: "2026-08-16T06:49:00Z",
      }),
      makeRun({
        id: 2,
        color: "blue",
        model_bags: 3,
        ended_at: "2026-08-16T07:04:00Z",
        last_counted_at: "2026-08-16T07:04:00Z",
      }),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
    expect(result[0].color).toBe("red");
    expect(result[0].model_bags).toBe(2463);
    // Конец диапазона дотянут до конца поглощённого вкрапления.
    expect(result[0].ended_at).toBe("2026-08-16T07:04:00Z");
    expect(result[0].last_counted_at).toBe("2026-08-16T07:04:00Z");
  });

  it("не склеивает крупный период (>= порога) и тот же цвет", () => {
    const result = smoothColorRuns([
      makeRun({ id: 1, color: "red", model_bags: 2460 }),
      makeRun({ id: 2, color: "blue", model_bags: 57 }), // крупный — остаётся
      makeRun({ id: 3, color: "red", model_bags: 3 }), // тот же цвет что и... нет, prev синий, но < 7 → склеится
    ]);

    // #2 крупный (57 >= 7) → якорь; #3 (red, 3 меш.) склеивается в синий #2.
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe(1);
    expect(result[0].model_bags).toBe(2460);
    expect(result[1].id).toBe(2);
    expect(result[1].model_bags).toBe(60);
  });

  it("серию мелких вкраплений подряд клеит к одному якорю", () => {
    const result = smoothColorRuns([
      makeRun({ id: 1, color: "blue", model_bags: 176 }),
      makeRun({ id: 2, color: "green", model_bags: 2 }),
      makeRun({ id: 3, color: "green", model_bags: 1 }),
      makeRun({ id: 4, color: "red", model_bags: 1 }),
    ]);

    // #2 (green,2) склеен в blue #1 → prev остаётся blue; #3 (green,1) тот же цвет
    // что и... prev теперь blue → green != blue и <7 → склеен; #4 (red,1) тоже <7 → склеен.
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
    expect(result[0].color).toBe("blue");
    expect(result[0].model_bags).toBe(180);
  });

  it("не трогает сквозные периоды", () => {
    const result = smoothColorRuns([
      makeRun({ id: 1, color: "red", model_bags: 100, is_partial_for_day: true }),
      makeRun({ id: 2, color: "blue", model_bags: 3 }),
      makeRun({ id: 3, color: "green", model_bags: 2, is_partial_for_day: true }),
    ]);

    // #2 не склеится: prev — сквозной. #3 сквозной сам по себе не поглощается.
    expect(result).toHaveLength(3);
    expect(result.map((run) => run.id)).toEqual([1, 2, 3]);
  });
});
