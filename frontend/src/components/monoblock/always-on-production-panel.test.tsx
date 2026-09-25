import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ apiError: (error: Error) => error.message }));

import type { ComponentProps } from "react";
import type { AlwaysOnProductionPayload, AlwaysOnProductionRun, AlwaysOnStockBatch } from "@/lib/types";
import {
  AlwaysOnDayColorViewToggle,
  AlwaysOnDayRunLog,
  AlwaysOnProductionPanel,
  buildReceiptMapping,
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

const activeRun = makeRun({
  id: 7,
  started_at: "2026-08-16T04:30:00Z",
  last_counted_at: "2026-08-16T04:45:00Z",
  ended_at: null,
  model_bags: 126,
  status: "active",
});

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
  algorithm_day_runs: [],
  run_smoothing: {
    n_min: 10,
    raw_model_total: 0,
    algorithm_model_total: 0,
    raw_colors: [],
    algorithm_colors: [],
  },
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
      warehouse_ids: [1],
    },
    {
      id: 2,
      label: "Мука синяя · 25 кг",
      color: "Blue",
      color_label: "Синий",
      weight_kg: "25.00",
      warehouse_ids: [1],
    },
    {
      id: 3,
      label: "Мука зелёная · 50 кг",
      color: "Green",
      color_label: "Зелёный",
      weight_kg: "50.00",
      warehouse_ids: [2],
    },
    {
      id: 4,
      label: "Мука красная второго склада · 50 кг",
      color: "Red",
      color_label: "Красный",
      weight_kg: "50.00",
      warehouse_ids: [2],
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
  unresolved: { business_day: "2026-08-16", bags: 0 },
  batches: [],
};

const failedBatch: AlwaysOnStockBatch = {
  id: 9,
  camera: "cam1",
  warehouse: 1,
  warehouse_name: "Склад 1",
  business_day: "2026-08-15",
  scheduled_for: "2026-08-15T14:00:00Z",
  status: "failed",
  total_bags: 20,
  pending_bags: 0,
  last_error: "Склад временно недоступен",
  attempts: 1,
  items: [],
};

/** Панель с правом управления и заглушками обработчиков; тест задаёт только важное ему. */
function panel(props: Partial<ComponentProps<typeof AlwaysOnProductionPanel>> = {}) {
  return (
    <AlwaysOnProductionPanel
      payload={payload}
      loading={false}
      error={null}
      saving={false}
      canManage
      onSave={vi.fn()}
      onRetry={vi.fn()}
      onAssignUnknown={vi.fn()}
      {...props}
    />
  );
}

describe("AlwaysOnProductionPanel", () => {
  it("показывает настройки автоприхода без общего журнала цветов", () => {
    render(panel());

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
      panel({
        payload: {
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
        },
      }),
    );

    const previewPanel = screen.getByText("Предварительный приход").closest('[data-testid="always-on-panel"]');
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
    const { rerender } = render(panel({ payload: configuredPayload }));

    expect(screen.getByText("готово")).toBeInTheDocument();
    rerender(
      panel({
        payload: {
          ...configuredPayload,
          fully_configured: false,
          available_colors: ["red", "blue"],
          mappings: [...configuredPayload.mappings, { color: "blue", product: null, product_label: null }],
        },
      }),
    );

    expect(screen.getByLabelText("Товар для цвета Синий")).toBeInTheDocument();
    expect(screen.getByText("нужна настройка")).toBeInTheDocument();
    expect(screen.getByText("1 из 2")).toBeInTheDocument();
  });

  it.each([
    ["white", "Белый"],
    ["unclassified", "Не определён"],
  ])("разрешает сопоставить цвет %s с товаром для нового склада", (color, colorLabel) => {
    render(
      panel({
        payload: {
          ...payload,
          fully_configured: false,
          available_colors: [color],
          mappings: [{ color, product: null, product_label: null }],
        },
      }),
    );

    const select = screen.getByLabelText(`Товар для цвета ${colorLabel}`);
    expect(within(select).getByRole("option", { name: "Мука красная · 50 кг" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "Мука синяя · 25 кг" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: /второго склада/ })).toBeInTheDocument();
  });

  it("показывает ошибочную привязку товара к другому складу как ненастроенную", () => {
    render(
      panel({
        payload: {
          ...payload,
          available_colors: ["red"],
          mappings: [{ color: "red", product: 4, product_label: "Мука красная второго склада · 50 кг" }],
          fully_configured: false,
        },
      }),
    );

    expect(screen.getByText("нужна настройка")).toBeInTheDocument();
    expect(screen.getByText("1 из 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Товар для цвета Красный")).toHaveValue("4");
  });

  it("позволяет повторно сохранить товар без карточки в складе камеры", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      panel({
        payload: {
          ...payload,
          available_colors: ["red"],
          mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
          products: payload.products.map((product) => (product.id === 1 ? { ...product, warehouse_ids: [] } : product)),
          fully_configured: false,
        },
        onSave,
      }),
    );

    expect(screen.getByLabelText("Товар для цвета Красный").closest("label")).toHaveTextContent("Не привязан");
    const save = screen.getByRole("button", { name: "Сохранить" });
    expect(save).toBeEnabled();
    await user.click(save);
    expect(onSave).toHaveBeenCalledWith([{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }], 1);
  });

  it("предлагает товар совпадающего цвета и сохраняет выбранное сопоставление", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(panel({ onSave }));

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

  it("changes the receipt warehouse and keeps mappings that can create a new stock card", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(panel({ onSave }));

    await user.selectOptions(screen.getByLabelText("Склад прихода"), "2");
    const red = screen.getByLabelText("Товар для цвета Красный");
    expect(red).toHaveValue("1");
    expect(within(red).getByRole("option", { name: "Мука красная · 50 кг" })).toBeInTheDocument();
    expect(within(red).getByRole("option", { name: "Мука красная второго склада · 50 кг" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith(
      [
        { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
        { color: "blue", product: null, product_label: null },
      ],
      2,
    );
  });

  it("keeps a mapped product when it already has a card in the next warehouse", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      panel({
        payload: {
          ...payload,
          products: payload.products.map((product) =>
            product.id === 1 ? { ...product, warehouse_ids: [1, 2] } : product,
          ),
        },
        onSave,
      }),
    );

    await user.selectOptions(screen.getByLabelText("Склад прихода"), "2");
    const red = screen.getByLabelText("Товар для цвета Красный");
    expect(red).toHaveValue("1");
    expect(within(red).getByRole("option", { name: "Мука красная · 50 кг" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith(
      [
        { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
        { color: "blue", product: null, product_label: null },
      ],
      2,
    );
  });

  it("оставляет настройки и повторный приход только для чтения без права управления", () => {
    const onRetry = vi.fn();
    render(panel({ payload: { ...payload, batches: [failedBatch] }, canManage: false, onRetry }));

    expect(screen.getByLabelText("Товар для цвета Красный")).toBeDisabled();
    expect(screen.getByLabelText("Товар для цвета Синий")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("разрешает повторить ошибочный приход с правом управления", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(panel({ payload: { ...payload, batches: [failedBatch] }, onRetry }));

    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(onRetry).toHaveBeenCalledWith(failedBatch);
  });

  it("не блокирует приход мешками без цвета и предлагает указать цвет", async () => {
    const user = userEvent.setup();
    const onAssignUnknown = vi.fn().mockResolvedValue(undefined);
    render(
      panel({
        payload: {
          ...payload,
          preview: [{ ...payload.preview[0], resolved_bags: 2, inferred: { neighbors: 2 } }],
          unresolved: { business_day: "2026-08-16", bags: 3 },
        },
        onAssignUnknown,
      }),
    );

    const previewPanel = screen.getByText("Предварительный приход").closest('[data-testid="always-on-panel"]');
    if (!(previewPanel instanceof HTMLElement)) throw new Error("Карточка предварительного прихода не найдена");
    expect(within(previewPanel).getByText("по соседям · 2")).toBeInTheDocument();
    expect(within(previewPanel).getByText("Цвет не определён: 3 мешка")).toBeInTheDocument();
    expect(within(previewPanel).queryByText("Не определён")).not.toBeInTheDocument();

    await user.click(within(previewPanel).getByRole("button", { name: "Указать цвет" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Причина"), "Проверено по записи");
    await user.click(within(dialog).getByRole("button", { name: "Указать цвет" }));

    expect(onAssignUnknown).toHaveBeenCalledWith({
      business_day: "2026-08-16",
      color: "red",
      bags: 3,
      reason: "Проверено по записи",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("показывает мешки без цвета у оприходованной смены", async () => {
    const user = userEvent.setup();
    const batch = {
      id: 11,
      camera: "cam1",
      warehouse: 1,
      warehouse_name: "Склад 1",
      business_day: "2026-08-15",
      scheduled_for: "2026-08-15T14:00:00Z",
      status: "posted" as const,
      total_bags: 120,
      pending_bags: 1,
      last_error: "",
      attempts: 1,
      items: [],
    };
    render(panel({ payload: { ...payload, batches: [batch] } }));

    const history = screen.getByText("История приходов").closest('[data-testid="always-on-panel"]');
    if (!(history instanceof HTMLElement)) throw new Error("История приходов не найдена");
    expect(within(history).getByText("Цвет не определён: 1 мешок")).toBeInTheDocument();
    expect(within(history).queryByText(/Не настроен товар/)).not.toBeInTheDocument();

    await user.click(within(history).getByRole("button", { name: "Указать цвет" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Смена 15.08.2026")).toBeInTheDocument();
    expect(within(dialog).getByText(/Цвет не определён: 1 мешок\./)).toBeInTheDocument();
    expect(within(dialog).getByText(/отдельным приходом/)).toBeInTheDocument();
  });

  it("без права управления только показывает мешки без цвета", () => {
    render(panel({ payload: { ...payload, unresolved: { business_day: "2026-08-16", bags: 2 } }, canManage: false }));

    expect(screen.getByText("Цвет не определён: 2 мешка")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Указать цвет" })).not.toBeInTheDocument();
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
        runs={[activeRun, makeRun({ id: 8, color: "blue", model_bags: 3 })]}
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

    const boundRun = screen.getByRole("group", { name: "Период Мука красная · 50 кг: 126 мешков" });
    const unboundRun = screen.getByRole("group", { name: "Период Синий: 3 мешков" });
    const bound = boundRun.querySelector('[data-receipt-binding="bound"]');
    const unbound = unboundRun.querySelector('[data-receipt-binding="unbound"]');
    expect(bound).toHaveTextContent("Приход — Мука красная · 50 кг→склад Основной склад");
    expect(within(boundRun).queryByText("Красный")).not.toBeInTheDocument();
    expect(boundRun.getElementsByClassName("bg-[#dc604d]")).toHaveLength(1);
    expect(within(unboundRun).getByText("Синий")).toBeInTheDocument();
    expect(unbound).toHaveTextContent("Синий: приход — Не привязан");
  });

  it("берёт привязки «Куда приходовать» из выбранного дня, а без него — из снимка вкладки", () => {
    const day = { ...payload, warehouse: 2, warehouse_name: "Склад №2", mappings: [] };
    expect(buildReceiptMapping(day, payload, null)).toEqual({
      status: "ready",
      mappings: [],
      products: payload.products,
      warehouse: 2,
      warehouseName: "Склад №2",
    });
    expect(buildReceiptMapping(null, payload, null)).toMatchObject({
      status: "ready",
      mappings: payload.mappings,
      warehouseName: "Основной склад",
    });
    expect(buildReceiptMapping(null, null, null).status).toBe("loading");
    expect(buildReceiptMapping(day, payload, "Сеть недоступна").status).toBe("unavailable");
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

  it("не показывает склад для товара без складской карточки", () => {
    expect(
      resolveAlwaysOnReceiptDestination(
        {
          status: "ready",
          mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
          products: payload.products.map((product) => (product.id === 1 ? { ...product, warehouse_ids: [] } : product)),
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
          activeRun,
          {
            ...activeRun,
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
    expect(screen.getByText("Красный")).toBeInTheDocument();
    expect(screen.getByText("Синий")).toBeInTheDocument();
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
            ...activeRun,
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
  });

  it("помечает мешки, цвет которых определила CRM, и показывает обе части разбитого периода", () => {
    render(
      <AlwaysOnDayRunLog
        day="2026-08-16"
        timezone="UTC"
        loading={false}
        error={null}
        runs={[
          makeRun({
            id: 5,
            color: "red",
            model_bags: 1,
            inferred: { votes: 1 },
            source_color: "unknown",
            segment: 0,
            started_at: "2026-08-16T10:00:00Z",
            last_counted_at: "2026-08-16T10:00:10Z",
          }),
          makeRun({
            id: 5,
            color: "blue",
            model_bags: 1,
            inferred: { votes: 1 },
            source_color: "unknown",
            segment: 1,
            started_at: "2026-08-16T10:00:20Z",
            last_counted_at: "2026-08-16T10:00:20Z",
          }),
          makeRun({ id: 6, color: "blue", model_bags: 8, started_at: "2026-08-16T10:01:00Z" }),
        ]}
      />,
    );

    const red = screen.getByRole("group", { name: "Период Красный: 1 мешков" });
    const blue = screen.getByRole("group", { name: "Период Синий: 1 мешков" });
    expect(within(red).getByText("по голосам · 1")).toBeInTheDocument();
    expect(within(blue).getByText("по голосам · 1")).toBeInTheDocument();
    const camera = screen.getByRole("group", { name: "Период Синий: 8 мешков" });
    expect(camera.querySelector("[data-inferred-badge]")).toBeNull();
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
