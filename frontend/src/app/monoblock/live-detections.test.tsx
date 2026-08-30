import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MonoblockPage from "./page";

const mocks = vi.hoisted(() => ({
  responses: new Map<string, unknown>(),
  apiGet: vi.fn(),
  permissions: ["shipping.load"],
  resolveDetections: null as null | ((value: { data: { processors: unknown[] } }) => void),
  rejectDetections: null as null | ((reason?: unknown) => void),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: {
      id: 1,
      username: "loader",
      is_client: false,
      is_superuser: false,
      is_monoblock: false,
      monoblock_name: null,
      monoblock_camera: null,
      permissions: mocks.permissions,
      position: null,
      client_id: null,
      sales_department: null,
    },
    loading: false,
  }),
}));

vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/shipping/shipment-launcher", () => ({
  ShipmentLauncher: () => null,
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ onStateChange }: { onStateChange?: (online: boolean) => void }) => (
    <button type="button" aria-label="Подключить тестовый поток" onClick={() => onStateChange?.(true)} />
  ),
  ensureCameraStreamToken: vi.fn(),
}));

vi.mock("@/components/detection-overlay", () => ({
  DetectionOverlay: ({ detections }: { detections?: Array<{ label?: string }> }) => (
    <div data-testid="detection-layer">
      {(detections ?? []).map((detection, index) => (
        <span key={`${detection.label}-${index}`}>{detection.label}</span>
      ))}
    </div>
  ),
}));

vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: () => undefined,
}));

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => ({
    data: url ? (mocks.responses.get(url) ?? null) : null,
    error: "",
    loading: false,
    reload: vi.fn().mockResolvedValue(undefined),
    setData: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mocks.apiGet(...args),
  },
  apiError: () => "Ошибка тестового API",
}));

const processor = {
  cam: "cam2",
  running: true,
  mode: "always_on",
  recording: false,
  total: 17,
  detections: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.4, label: "Red_50", confidence: 0.91, counted: false }],
};

const alwaysOnSettings = {
  camera_sources: ["cam2"],
  source: "sub",
  processors: [processor],
  capacity: 2,
  service_available: true,
  sync_status: "synced",
  detail: "",
  updated_at: null,
};

const analytics = {
  day: "2026-08-24",
  total: 17,
  all_time_total: 17,
  model_all_time_total: 17,
  adjustment: 0,
  history: [],
  colors: [],
  dominant_color: null,
  cameras: [],
};

beforeEach(() => {
  mocks.permissions = ["shipping.load"];
  mocks.responses = new Map<string, unknown>([
    ["/orders/?post_board=1", []],
    [
      "/cameras/",
      [
        {
          id: "nvr:cam2",
          name: "cam2",
          zone: "Робот Кука",
          src: "cam2",
          kind: "nvr-channel",
          online: true,
        },
      ],
    ],
    ["/cameras/ai/sessions/", []],
    [
      "/cameras/monoblock-settings/",
      { camera_sources: [], locked: false, device_id: null, device_name: null, updated_at: null },
    ],
    ["/cameras/always-on-settings/", alwaysOnSettings],
    ["/cameras/always-on-analytics/", analytics],
  ]);

  const detections = new Promise<{ data: { processors: unknown[] } }>((resolve, reject) => {
    mocks.resolveDetections = resolve;
    mocks.rejectDetections = reject;
  });
  mocks.apiGet.mockImplementation((url: unknown) => {
    if (url === "/cameras/always-on-detections/") return detections;
    if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: alwaysOnSettings });
    if (url === "/cameras/always-on-analytics/") return Promise.resolve({ data: analytics });
    return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
  });
});

describe("AI 24/7 live detections", () => {
  it("не показывает независимую бренд-разбивку в активной аналитике", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["shipping.load", "ai_247.manage"];
    const brandedAnalytics = {
      ...analytics,
      total: 29,
      all_time_total: 29,
      model_all_time_total: 29,
      model_per_brand: { future_brand: 12, korol: 9, dikhan_baba: 5, unknown: 2, unclassified: 1 },
      brands: [
        { brand: "future_brand", total: 12, percent: 41.4 },
        { brand: "korol", total: 9, percent: 31 },
        { brand: "dikhan_baba", total: 5, percent: 17.2 },
        { brand: "unknown", total: 2, percent: 6.9 },
        { brand: "unclassified", total: 1, percent: 3.5 },
      ],
      dominant_brand: "korol",
      cameras: [
        {
          camera: "cam2",
          day: "2026-08-24",
          model_total: 29,
          model_per_color: {},
          model_per_brand: { future_brand: 12, korol: 9, dikhan_baba: 5, unknown: 2, unclassified: 1 },
          adjustment: 0,
          total: 29,
          all_time_total: 29,
          history: [],
          colors: [],
          brands: [
            { brand: "future_brand", total: 12, percent: 41.4 },
            { brand: "korol", total: 9, percent: 31 },
            { brand: "dikhan_baba", total: 5, percent: 17.2 },
            { brand: "unknown", total: 2, percent: 6.9 },
            { brand: "unclassified", total: 1, percent: 3.5 },
          ],
          dominant_color: null,
          dominant_brand: "korol",
          updated_at: null,
        },
      ],
    };
    mocks.responses.set("/cameras/always-on-analytics/", brandedAnalytics);
    mocks.apiGet.mockImplementation((url: unknown) => {
      if (url === "/cameras/always-on-detections/") {
        return Promise.resolve({ data: { processors: [processor] } });
      }
      if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: alwaysOnSettings });
      if (url === "/cameras/always-on-analytics/") return Promise.resolve({ data: brandedAnalytics });
      return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
    });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("tab", { name: "Аналитика" }));

    expect(screen.queryByText("Основной бренд")).not.toBeInTheDocument();
    expect(screen.queryByText("Бренды")).not.toBeInTheDocument();
    expect(screen.queryByText("Korol")).not.toBeInTheDocument();
    expect(screen.queryByText("Future Brand")).not.toBeInTheDocument();
    expect(screen.queryByText("Дихан Баба")).not.toBeInTheDocument();
    expect(screen.getByText("Цвета продукции")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Уменьшить/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сдать в архив/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Архив" })).not.toBeInTheDocument();
  });

  it("переключает карточки цветов и периоды дня между алгоритмом и сырыми данными", async () => {
    const user = userEvent.setup();
    const day = "2026-08-24";
    const historyPoint = {
      day,
      model_total: 158,
      model_per_color: { red: 150, green: 5, blue: 3 },
      model_per_brand: { korol: 158 },
      colors: [
        { color: "red", total: 150, percent: 94.9 },
        { color: "green", total: 5, percent: 3.2 },
        { color: "blue", total: 3, percent: 1.9 },
      ],
      brands: [{ brand: "korol", total: 158, percent: 100 }],
      adjustment: -5,
      total: 153,
      updated_at: null,
    };
    const legacyDay = "2026-08-23";
    const legacyHistoryPoint = {
      day: legacyDay,
      model_total: 12,
      model_per_color: { red: 10, blue: 2 },
      model_per_brand: {},
      colors: [
        { color: "red", total: 10, percent: 83.3 },
        { color: "blue", total: 2, percent: 16.7 },
      ],
      brands: [],
      adjustment: 0,
      total: 12,
      updated_at: null,
    };
    const archivedDay = "2026-08-22";
    const archivedHistoryPoint = {
      day: archivedDay,
      model_total: 40,
      model_per_color: { blue: 40 },
      model_per_brand: {},
      colors: [{ color: "blue", total: 40, percent: 100 }],
      brands: [],
      adjustment: 0,
      total: 40,
      updated_at: null,
    };
    const peakHistoryPoint = {
      day: "2026-08-21",
      model_total: 200,
      model_per_color: { red: 200 },
      model_per_brand: {},
      colors: [{ color: "red", total: 200, percent: 100 }],
      brands: [],
      adjustment: -20,
      total: 180,
      updated_at: null,
    };
    const detailedAnalytics = {
      ...analytics,
      total: 153,
      all_time_total: 385,
      model_all_time_total: 410,
      history: [peakHistoryPoint, archivedHistoryPoint, legacyHistoryPoint, historyPoint],
      colors: historyPoint.colors,
      model_per_brand: historyPoint.model_per_brand,
      brands: historyPoint.brands,
      dominant_color: "red",
      dominant_brand: "korol",
      cameras: [
        {
          camera: "cam2",
          day,
          model_total: 158,
          model_per_color: historyPoint.model_per_color,
          model_per_brand: historyPoint.model_per_brand,
          adjustment: -5,
          total: 153,
          all_time_total: 385,
          history: [peakHistoryPoint, archivedHistoryPoint, legacyHistoryPoint, historyPoint],
          colors: historyPoint.colors,
          brands: historyPoint.brands,
          dominant_color: "red",
          dominant_brand: "korol",
          updated_at: null,
        },
      ],
    };
    const rawRuns = [
      {
        id: 1,
        camera: "cam2",
        business_day: day,
        color: "green",
        started_at: "2026-08-24T06:50:00Z",
        last_counted_at: "2026-08-24T06:59:00Z",
        ended_at: "2026-08-24T06:59:00Z",
        model_bags: 5,
        is_approximate: true,
        status: "closed",
      },
      {
        id: 2,
        camera: "cam2",
        business_day: day,
        color: "red",
        started_at: "2026-08-24T07:00:00Z",
        last_counted_at: "2026-08-24T08:00:00Z",
        ended_at: "2026-08-24T08:00:00Z",
        model_bags: 100,
        is_approximate: false,
        status: "closed",
      },
      {
        id: 3,
        camera: "cam2",
        business_day: day,
        color: "blue",
        started_at: "2026-08-24T08:01:00Z",
        last_counted_at: "2026-08-24T08:02:00Z",
        ended_at: "2026-08-24T08:02:00Z",
        model_bags: 3,
        is_approximate: false,
        status: "closed",
      },
      {
        id: 4,
        camera: "cam2",
        business_day: day,
        color: "red",
        started_at: "2026-08-24T08:03:00Z",
        last_counted_at: "2026-08-24T09:00:00Z",
        ended_at: "2026-08-24T09:00:00Z",
        model_bags: 50,
        is_approximate: false,
        status: "closed",
      },
    ];
    const algorithmRuns = [
      rawRuns[0],
      {
        ...rawRuns[1],
        last_counted_at: rawRuns[3].last_counted_at,
        ended_at: rawRuns[3].ended_at,
        model_bags: 153,
      },
    ];
    const productionDay = {
      selected_day: day,
      timezone: "UTC",
      dominant_brand_by_color: {
        red: "dikhan_baba",
        green: "korol",
        blue: "korol",
      },
      mappings: [
        { color: "red", product: 1, product_label: "ДБН 1с 50кг · Красный 50 кг" },
        { color: "green", product: 2, product_label: "K2c 50кг · Зелёный 50 кг" },
        { color: "blue", product: 3, product_label: "ДБН вс 50кг · Синий 50 кг" },
      ],
      day_runs: rawRuns,
      algorithm_day_runs: algorithmRuns,
      run_smoothing: {
        n_min: 10,
        changed: true,
        raw_run_count: 4,
        algorithm_run_count: 2,
        raw_model_total: 158,
        algorithm_model_total: 158,
        raw_model_per_color: { red: 150, green: 5, blue: 3 },
        algorithm_model_per_color: { red: 153, green: 5 },
        raw_colors: historyPoint.colors,
        algorithm_colors: [
          { color: "red", total: 153, percent: 96.8 },
          { color: "green", total: 5, percent: 3.2 },
        ],
      },
    };
    const productionUrl = `/cameras/always-on-production/?camera=cam2&day=${day}`;
    const legacyProductionUrl = `/cameras/always-on-production/?camera=cam2&day=${legacyDay}`;
    const archivedProductionUrl = `/cameras/always-on-production/?camera=cam2&day=${archivedDay}`;
    const legacyProductionDay = {
      selected_day: legacyDay,
      timezone: "UTC",
      day_runs: [
        {
          ...rawRuns[1],
          id: 11,
          business_day: legacyDay,
          started_at: "2026-08-23T07:00:00Z",
          last_counted_at: "2026-08-23T08:00:00Z",
          ended_at: "2026-08-23T08:00:00Z",
          model_bags: 10,
        },
        {
          ...rawRuns[2],
          id: 12,
          business_day: legacyDay,
          started_at: "2026-08-23T08:01:00Z",
          last_counted_at: "2026-08-23T08:02:00Z",
          ended_at: "2026-08-23T08:02:00Z",
          model_bags: 2,
        },
      ],
    };
    const archivedRawRuns = [
      {
        ...rawRuns[1],
        id: 21,
        business_day: archivedDay,
        started_at: "2026-08-22T07:00:00Z",
        last_counted_at: "2026-08-22T08:00:00Z",
        ended_at: "2026-08-22T08:00:00Z",
        model_bags: 100,
      },
      {
        ...rawRuns[2],
        id: 22,
        business_day: archivedDay,
        started_at: "2026-08-22T08:01:00Z",
        last_counted_at: "2026-08-22T08:30:00Z",
        ended_at: "2026-08-22T08:30:00Z",
        model_bags: 40,
      },
    ];
    const archivedProductionDay = {
      selected_day: archivedDay,
      timezone: "UTC",
      day_runs: archivedRawRuns,
      algorithm_day_runs: archivedRawRuns,
      run_smoothing: {
        n_min: 10,
        changed: false,
        raw_run_count: 2,
        algorithm_run_count: 2,
        raw_model_total: 140,
        algorithm_model_total: 140,
        raw_model_per_color: { red: 100, blue: 40 },
        algorithm_model_per_color: { red: 100, blue: 40 },
        raw_colors: [
          { color: "red", total: 100, percent: 71.4 },
          { color: "blue", total: 40, percent: 28.6 },
        ],
        algorithm_colors: [
          { color: "red", total: 100, percent: 71.4 },
          { color: "blue", total: 40, percent: 28.6 },
        ],
      },
    };

    mocks.responses.set("/cameras/always-on-analytics/", detailedAnalytics);
    mocks.apiGet.mockImplementation((url: unknown) => {
      if (url === "/cameras/always-on-detections/") {
        return Promise.resolve({ data: { processors: [processor] } });
      }
      if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: alwaysOnSettings });
      if (url === "/cameras/always-on-analytics/") return Promise.resolve({ data: detailedAnalytics });
      if (url === productionUrl) return Promise.resolve({ data: productionDay });
      if (url === legacyProductionUrl) return Promise.resolve({ data: legacyProductionDay });
      if (url === archivedProductionUrl) return Promise.resolve({ data: archivedProductionDay });
      return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
    });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("tab", { name: "Аналитика" }));
    await user.click(screen.getByRole("button", { name: "Аналитика за 24.08.2026: 153 мешков" }));

    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith(productionUrl));
    const heading = screen.getByRole("heading", { name: "24.08.2026" });
    const dayPanel = heading.closest(".rounded-2xl");
    if (!(dayPanel instanceof HTMLElement)) throw new Error("Карточка выбранного дня не найдена");

    const bagsMetric = within(dayPanel).getByText("Учтено за день").parentElement;
    const maxMetric = within(dayPanel).getByText("От максимума").parentElement;
    if (!(bagsMetric instanceof HTMLElement) || !(maxMetric instanceof HTMLElement)) {
      throw new Error("Краткие метрики выбранного дня не найдены");
    }
    expect(within(bagsMetric).getByText("153")).toBeInTheDocument();
    expect(within(maxMetric).getByText("85%")).toBeInTheDocument();
    expect(within(dayPanel).queryByText("Итог")).not.toBeInTheDocument();
    expect(within(dayPanel).queryByText("Модель")).not.toBeInTheDocument();
    expect(within(dayPanel).queryByText("Поправка")).not.toBeInTheDocument();
    expect(within(dayPanel).getByText("Цвета и продукция за день")).toBeInTheDocument();

    const algorithmButton = within(dayPanel).getByRole("button", { name: "Алгоритм" });
    expect(algorithmButton).toHaveAttribute("aria-pressed", "true");
    const algorithmRed = await within(dayPanel).findByRole("group", { name: "Красный: 153 мешков" });
    expect(within(algorithmRed).getByText("96.8%")).toBeInTheDocument();
    const redMapping = within(algorithmRed).getByText("ДБН 1с 50кг · Красный 50 кг");
    const redColorAndBrand = within(algorithmRed).getByText("Красный · Дихан Баба");
    expect(redMapping.compareDocumentPosition(redColorAndBrand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const algorithmGreen = within(dayPanel).getByRole("group", { name: "Зелёный: 5 мешков" });
    expect(within(algorithmGreen).getByText("K2c 50кг · Зелёный 50 кг")).toBeInTheDocument();
    expect(within(algorithmGreen).getByText("Зелёный · Korol")).toBeInTheDocument();
    expect(within(dayPanel).queryByRole("group", { name: /Синий: / })).not.toBeInTheDocument();
    expect(within(dayPanel).getAllByText("меш.")).toHaveLength(2);
    expect(within(dayPanel).queryByText("Korol")).not.toBeInTheDocument();
    expect(within(dayPanel).queryByText("Бренды за день")).not.toBeInTheDocument();

    await user.click(within(dayPanel).getByRole("button", { name: "Сырые данные" }));

    expect(within(dayPanel).getByRole("button", { name: "Сырые данные" })).toHaveAttribute("aria-pressed", "true");
    const rawRed = within(dayPanel).getByRole("group", { name: "Красный: 150 мешков" });
    expect(within(rawRed).getByText("94.9%")).toBeInTheDocument();
    expect(within(dayPanel).getByRole("group", { name: "Зелёный: 5 мешков" })).toBeInTheDocument();
    const rawBlue = within(dayPanel).getByRole("group", { name: "Синий: 3 мешков" });
    expect(within(rawBlue).getByText("ДБН вс 50кг · Синий 50 кг")).toBeInTheDocument();
    expect(within(rawBlue).getByText("Синий · Korol")).toBeInTheDocument();
    expect(within(dayPanel).getAllByText("меш.")).toHaveLength(4);
    expect(within(dayPanel).queryByText("Korol")).not.toBeInTheDocument();

    // Предыдущий API не знает об algorithm_day_runs/run_smoothing: оба
    // режима должны без ошибки показать исходный дневной срез.
    await user.click(screen.getByRole("button", { name: "Аналитика за 23.08.2026: 12 мешков" }));
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith(legacyProductionUrl));
    const legacyHeading = screen.getByRole("heading", { name: "23.08.2026" });
    const legacyDayPanel = legacyHeading.closest(".rounded-2xl");
    if (!(legacyDayPanel instanceof HTMLElement)) throw new Error("Карточка legacy-дня не найдена");
    await waitFor(() =>
      expect(within(legacyDayPanel).getByRole("button", { name: "Алгоритм" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(within(legacyDayPanel).getByRole("group", { name: "Красный: 10 мешков" })).toBeInTheDocument();
    expect(within(legacyDayPanel).getByRole("group", { name: "Синий: 2 мешков" })).toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("Сопоставление недоступно")).toHaveLength(2);
    expect(within(legacyDayPanel).getAllByText(/Бренд недоступен$/)).toHaveLength(2);
    expect(within(legacyDayPanel).queryByText("Не сопоставлено")).not.toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("меш.")).toHaveLength(2);

    await user.click(within(legacyDayPanel).getByRole("button", { name: "Сырые данные" }));
    expect(within(legacyDayPanel).getByRole("group", { name: "Красный: 10 мешков" })).toBeInTheDocument();
    expect(within(legacyDayPanel).getByRole("group", { name: "Синий: 2 мешков" })).toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("меш.")).toHaveLength(2);

    // Append-only production runs include the part already moved to an
    // archive. Never mix that full ledger (140) with the active slice (40).
    await user.click(screen.getByRole("button", { name: "Аналитика за 22.08.2026: 40 мешков" }));
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith(archivedProductionUrl));
    const archivedHeading = screen.getByRole("heading", { name: "22.08.2026" });
    const archivedDayPanel = archivedHeading.closest(".rounded-2xl");
    if (!(archivedDayPanel instanceof HTMLElement)) throw new Error("Карточка архивного среза не найдена");
    expect(await within(archivedDayPanel).findByRole("group", { name: "Синий: 40 мешков" })).toBeInTheDocument();
    expect(within(archivedDayPanel).queryByRole("group", { name: /Красный: / })).not.toBeInTheDocument();
    expect(within(archivedDayPanel).getByRole("status")).toHaveTextContent("часть дня уже перенесена в архив");
    expect(within(archivedDayPanel).queryByText("меш.")).not.toBeInTheDocument();
  });

  it("clears the last snapshot when the authoritative poll no longer contains this processor", async () => {
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("button", { name: "Подключить тестовый поток" }));
    expect(screen.getByText("Red_50")).toBeInTheDocument();

    await act(async () => {
      mocks.resolveDetections?.({ data: { processors: [] } });
    });
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith("/cameras/always-on-detections/"));

    // An empty successful response is authoritative. Falling back to the
    // initial settings snapshot gives the old box a fresh timestamp on every
    // poll, so it can otherwise remain visible forever.
    expect(screen.queryByText("Red_50")).not.toBeInTheDocument();
  });

  it("clears the last snapshot when the fast detection endpoint fails", async () => {
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("button", { name: "Подключить тестовый поток" }));
    expect(screen.getByText("Red_50")).toBeInTheDocument();

    await act(async () => {
      mocks.rejectDetections?.(new Error("camera PC unavailable"));
    });
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith("/cameras/always-on-detections/"));

    expect(screen.queryByText("Red_50")).not.toBeInTheDocument();
  });
});
