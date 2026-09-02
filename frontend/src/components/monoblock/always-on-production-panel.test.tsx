import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AlwaysOnProductionPayload, AlwaysOnProductionRun } from "@/lib/types";
import {
  AlwaysOnDayColorViewToggle,
  AlwaysOnDayRunLog,
  AlwaysOnProductionPanel,
  resolveAlwaysOnReceiptDestination,
} from "./always-on-production-panel";

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
  warehouse: 1,
  warehouse_name: "Основной склад",
  warehouses: [
    { id: 1, code: "main", name: "Основной склад", address: "", is_active: true, is_default: true },
    { id: 2, code: "second", name: "Склад №2", address: "Цех 2", is_active: true, is_default: false },
  ],
  timezone: "Asia/Almaty",
  close_time: "19:00",
  current_business_day: "2026-08-16",
  next_run_at: "2026-08-16T14:00:00Z",
  selected_day: null,
  day_runs: [],
  dominant_brand_by_color: {},
  fully_configured: false,
  available_colors: ["red", "blue"],
  mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
  products: [
    {
      id: 1,
      label: "Мука красная · 50 кг",
      color: "Red",
      color_label: "Красный",
      weight_kg: "50.00",
      warehouse: 1,
    },
    {
      id: 2,
      label: "Мука синяя · 25 кг",
      color: "Blue",
      color_label: "Синий",
      weight_kg: "25.00",
      warehouse: 1,
    },
    {
      id: 3,
      label: "Мука зелёная · 50 кг",
      color: "Green",
      color_label: "Зелёный",
      weight_kg: "50.00",
      warehouse: 2,
    },
    {
      id: 4,
      label: "Мука красная второго склада · 50 кг",
      color: "Red",
      color_label: "Красный",
      weight_kg: "50.00",
      warehouse: 2,
    },
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
    expect(screen.getByText("Автоприход")).toBeInTheDocument();
    expect(screen.getByText("19:00")).toBeInTheDocument();
    // Служебные фразы убраны — статус про ненастроенные цвета показывается
    // короткой плашкой (missingColors = [Синий] → «1 из 2»).
    expect(screen.getByText("нужна настройка")).toBeInTheDocument();
    expect(screen.getByText("1 из 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Товар для цвета Синий")).toBeInTheDocument();
  });

  it("показывает товар и склад в предварительном приходе, а отсутствие привязки — красным", () => {
    render(
      <AlwaysOnProductionPanel
        payload={{
          ...payload,
          preview: [
            ...payload.preview,
            {
              color: "blue",
              detected_bags: 4,
              correction_bags: 0,
              net_bags: 4,
              product: null,
              product_label: null,
              configured: false,
            },
          ],
        }}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

    const previewPanel = screen.getByText("Предварительный приход").closest(".rounded-2xl");
    if (!(previewPanel instanceof HTMLElement)) throw new Error("Карточка предварительного прихода не найдена");
    const bound = within(previewPanel).getByText("Мука красная · 50 кг").closest('[data-receipt-binding="bound"]');
    const unbound = previewPanel.querySelector('[data-receipt-binding="unbound"]');
    expect(bound).toHaveTextContent("Красный: приход — Мука красная · 50 кг→склад Основной склад");
    expect(unbound).toHaveTextContent("Синий: приход — Не привязан");
    expect(unbound).toHaveClass("text-red-700");
  });

  it("принимает новый непривязанный цвет из polling, когда оператор не редактировал форму", () => {
    const configuredPayload: AlwaysOnProductionPayload = {
      ...payload,
      fully_configured: true,
      available_colors: ["red"],
      mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
    };
    const { rerender } = render(
      <AlwaysOnProductionPanel
        payload={configuredPayload}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByText("готово")).toBeInTheDocument();
    rerender(
      <AlwaysOnProductionPanel
        payload={{
          ...configuredPayload,
          fully_configured: false,
          available_colors: ["red", "blue"],
          mappings: [...configuredPayload.mappings, { color: "blue", product: null, product_label: null }],
        }}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Товар для цвета Синий")).toBeInTheDocument();
    expect(screen.getByText("нужна настройка")).toBeInTheDocument();
    expect(screen.getByText("1 из 2")).toBeInTheDocument();
  });

  it.each([
    ["white", "Белый"],
    ["unclassified", "unclassified"],
  ])("разрешает сопоставить цвет %s с товаром выбранного склада", (color, colorLabel) => {
    render(
      <AlwaysOnProductionPanel
        payload={{
          ...payload,
          fully_configured: false,
          available_colors: [color],
          mappings: [{ color, product: null, product_label: null }],
        }}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

    const select = screen.getByLabelText(`Товар для цвета ${colorLabel}`);
    expect(within(select).getByRole("option", { name: "Мука красная · 50 кг" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "Мука синяя · 25 кг" })).toBeInTheDocument();
    expect(within(select).queryByRole("option", { name: /второго склада/ })).not.toBeInTheDocument();
  });

  it("показывает ошибочную привязку товара к другому складу как ненастроенную", () => {
    render(
      <AlwaysOnProductionPanel
        payload={{
          ...payload,
          available_colors: ["red"],
          mappings: [{ color: "red", product: 4, product_label: "Мука красная второго склада · 50 кг" }],
          fully_configured: false,
        }}
        loading={false}
        error={null}
        saving={false}
        canManage
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByText("нужна настройка")).toBeInTheDocument();
    expect(screen.getByText("1 из 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Товар для цвета Красный")).toHaveValue("4");
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

    expect(onSave).toHaveBeenCalledWith(
      [
        { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
        { color: "blue", product: 2, product_label: "Мука синяя · 25 кг" },
      ],
      1,
    );
  });

  it("changes the receipt warehouse and clears mappings owned by the previous warehouse", async () => {
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

    await user.selectOptions(screen.getByLabelText("Склад прихода"), "2");
    const red = screen.getByLabelText("Товар для цвета Красный");
    expect(red).toHaveValue("");
    expect(within(red).queryByRole("option", { name: "Мука красная · 50 кг" })).not.toBeInTheDocument();
    await user.selectOptions(red, "4");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith(
      [
        { color: "red", product: 4, product_label: "Мука красная второго склада · 50 кг" },
        { color: "blue", product: null, product_label: null },
      ],
      2,
    );
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
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
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

    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(onRetry).toHaveBeenCalledWith(batch);
  });
});

describe("AlwaysOnDayRunLog", () => {
  it("показывает текущий товар и склад для каждого периода и красный статус без привязки", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="Asia/Almaty"
        loading={false}
        error={null}
        runs={[payload.runs[0], makeRun({ id: 8, color: "blue", model_bags: 3 })]}
        receiptMapping={{
          status: "ready",
          mappings: [
            { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
            { color: "blue", product: null, product_label: null },
          ],
          products: payload.products,
          warehouse: payload.warehouse,
          warehouseName: payload.warehouse_name,
        }}
      />,
    );

    const bound = screen.getByText("Мука красная · 50 кг").closest('[data-receipt-binding="bound"]');
    const unbound = document.querySelector('[data-receipt-binding="unbound"]');
    expect(bound).toHaveTextContent("Красный: приход — Мука красная · 50 кг→склад Основной склад");
    expect(unbound).toHaveTextContent("Синий: приход — Не привязан");
  });

  it("не считает товар другого склада корректной привязкой", () => {
    expect(
      resolveAlwaysOnReceiptDestination(
        {
          status: "ready",
          mappings: [{ color: "red", product: 4, product_label: "Мука красная второго склада · 50 кг" }],
          products: payload.products,
          warehouse: 1,
          warehouseName: "Основной склад",
        },
        "red",
      ),
    ).toEqual({ state: "unbound" });
  });

  it("не смешивает журнал с несовместимым срезом аналитики", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="UTC"
        loading={false}
        error={null}
        unavailableReason="Периоды не показаны: часть дня уже перенесена в архив."
        runs={[makeRun({ color: "red", model_bags: 140 })]}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("часть дня уже перенесена в архив");
    expect(screen.queryByText("140")).not.toBeInTheDocument();
    expect(screen.queryByText("Красный")).not.toBeInTheDocument();
  });

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

    expect(screen.getByText(/15\.08\.2026/)).toHaveTextContent("Детализация");
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

  it("сохраняет каждую смену цвета отдельным диапазоном", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="UTC"
        loading={false}
        error={null}
        runs={[
          makeRun({
            id: 4,
            color: "red",
            model_bags: 3,
            started_at: "2026-08-16T19:46:00Z",
            last_counted_at: "2026-08-16T19:47:00Z",
            ended_at: "2026-08-16T19:47:00Z",
          }),
          makeRun({
            id: 2,
            color: "green",
            model_bags: 1,
            started_at: "2026-08-16T19:44:00Z",
            last_counted_at: "2026-08-16T19:45:00Z",
            ended_at: "2026-08-16T19:45:00Z",
          }),
          makeRun({
            id: 1,
            color: "red",
            model_bags: 4,
            started_at: "2026-08-16T19:00:00Z",
            last_counted_at: "2026-08-16T19:44:00Z",
            ended_at: "2026-08-16T19:44:00Z",
          }),
          makeRun({
            id: 3,
            color: "blue",
            model_bags: 2,
            started_at: "2026-08-16T19:45:00Z",
            last_counted_at: "2026-08-16T19:46:00Z",
            ended_at: "2026-08-16T19:46:00Z",
          }),
        ]}
      />,
    );

    const rows = screen.getAllByText("меш.").map((label) => {
      const row = label.closest("div.grid");
      if (!(row instanceof HTMLElement)) throw new Error("Строка периода не найдена");
      return row;
    });
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByText("Красный")).toBeInTheDocument();
    expect(within(rows[0]).getByText("4")).toBeInTheDocument();
    expect(within(rows[0]).getByText("19:00")).toBeInTheDocument();
    expect(within(rows[0]).getByText("19:44")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Зелёный")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Синий")).toBeInTheDocument();
    expect(within(rows[2]).getByText("2")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Красный")).toBeInTheDocument();
    expect(within(rows[3]).getByText("3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сглажено" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сырой" })).not.toBeInTheDocument();
  });
});

describe("AlwaysOnDayColorViewToggle", () => {
  it("остаётся видимым и сообщает о выборе сырых данных", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<AlwaysOnDayColorViewToggle view="algorithm" nMin={10} onChange={onChange} />);

    expect(screen.getByRole("button", { name: "Алгоритм" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Сырые данные" })).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: "Сырые данные" }));
    expect(onChange).toHaveBeenCalledWith("raw");

    rerender(<AlwaysOnDayColorViewToggle view="raw" nMin={10} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Сырые данные" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Алгоритм" })).toHaveAttribute("aria-pressed", "false");
  });
});
