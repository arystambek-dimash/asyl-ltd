import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MonoblockPage from "./page";
import type { AlwaysOnDailyAnalytics, ShippingCameraDayHistory } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  responses: new Map<string, unknown>(),
  requestedUrls: [] as string[],
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiPost: vi.fn(),
  permissions: ["shipping.load"],
  isSuperuser: false,
  resolveDetections: null as null | ((value: { data: { processors: unknown[] } }) => void),
  rejectDetections: null as null | ((reason?: unknown) => void),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: {
      id: 1,
      username: "loader",
      is_client: false,
      is_superuser: mocks.isSuperuser,

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
vi.mock("@/components/monoblock/shipping-transport-camera", () => ({
  ShippingTransportCamera: ({ conveyorCamera }: { conveyorCamera: string }) => (
    <div data-testid="transport-camera">{conveyorCamera}</div>
  ),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

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
    get: async (url: string, ...args: unknown[]) => {
      mocks.requestedUrls.push(url);
      if (url.includes("-analytics/?")) {
        const parsed = new URL(url, "http://localhost");
        const response: { data: AlwaysOnDailyAnalytics } = await mocks.apiGet(parsed.pathname, ...args);
        return {
          data: {
            ...response.data,
            cameras: response.data.cameras.map((camera) => ({
              ...camera,
              date_from: parsed.searchParams.get("date_from"),
              date_to: parsed.searchParams.get("date_to"),
              period_total: camera.total,
            })),
          },
        };
      }
      return mocks.apiGet(url, ...args);
    },
    put: (...args: unknown[]) => mocks.apiPut(...args),
    post: (...args: unknown[]) => mocks.apiPost(...args),
  },
  apiError: () => "Ошибка тестового API",
}));

vi.mock("@/lib/use-local-day", () => ({ useLocalDay: () => "2026-08-24" }));

const processor = {
  cam: "cam2",
  running: true,
  processor_alive: true,
  mode: "always_on",
  analytics_scope: "ai_247",
  source: "sub",
  recording: false,
  total: 17,
  detections: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.4, label: "Red_50", confidence: 0.91, counted: false }],
};

const alwaysOnSettings = {
  camera_sources: ["cam2"],
  analytics_scope: "ai_247",
  source: "sub",
  processors: [processor],
  capacity: 2,
  service_available: true,
  sync_status: "synced",
  detail: "",
  camera_readiness: { cam2: { status: "synced", detail: "" } },
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

const shippingHistoryUrl = (day: string) => `/cameras/shipping-continuous-history/?camera=cam2&day=${day}`;

function shippingDayHistory(day = "2026-08-24"): ShippingCameraDayHistory {
  const active = day === "2026-08-24";
  const rawRuns: ShippingCameraDayHistory["day_runs"] = [
    {
      id: 1,
      camera: "cam2",
      business_day: day,
      color: "red",
      started_at: `${day}T09:00:00+05:00`,
      last_counted_at: `${day}T09:01:00+05:00`,
      ended_at: `${day}T09:01:00+05:00`,
      model_bags: 4,
      is_approximate: false,
      status: "closed",
    },
    {
      id: 5,
      camera: "cam2",
      business_day: day,
      color: "blue",
      started_at: `${day}T09:02:00+05:00`,
      last_counted_at: `${day}T09:03:00+05:00`,
      ended_at: `${day}T09:03:00+05:00`,
      model_bags: 3,
      is_approximate: false,
      status: "closed",
    },
    {
      id: 8,
      camera: "cam2",
      business_day: day,
      color: "red",
      started_at: `${day}T09:04:00+05:00`,
      last_counted_at: `${day}T09:05:00+05:00`,
      ended_at: active ? null : `${day}T09:05:00+05:00`,
      model_bags: 5,
      is_approximate: false,
      status: active ? "active" : "closed",
    },
  ];
  return {
    camera: "cam2",
    timezone: "Asia/Almaty",
    selected_day: day,
    history_status: "complete",
    history_detail: "",
    day_runs: rawRuns,
    algorithm_day_runs: [
      {
        ...rawRuns[0],
        model_bags: 12,
        last_counted_at: rawRuns[2].last_counted_at,
        ended_at: rawRuns[2].ended_at,
        status: rawRuns[2].status,
      },
    ],
    run_smoothing: {
      n_min: 10,
      changed: true,
      raw_run_count: 3,
      algorithm_run_count: 1,
      raw_model_total: 12,
      algorithm_model_total: 12,
      raw_model_per_color: { red: 9, blue: 3 },
      algorithm_model_per_color: { red: 12 },
      raw_colors: [
        { color: "red", total: 9, percent: 75 },
        { color: "blue", total: 3, percent: 25 },
      ],
      algorithm_colors: [{ color: "red", total: 12, percent: 100 }],
    },
  };
}

function setupShippingHistory(
  getHistory: (day: string, options?: { signal?: AbortSignal }) => Promise<{ data: ShippingCameraDayHistory }> = async (
    day,
  ) => ({ data: shippingDayHistory(day) }),
) {
  const history = ["2026-08-23", "2026-08-24"].map((day) => ({
    day,
    model_total: 12,
    model_per_color: { red: 9, blue: 3 },
    model_per_brand: {},
    colors: [
      { color: "red", total: 9, percent: 75 },
      { color: "blue", total: 3, percent: 25 },
    ],
    brands: [],
    adjustment: 0,
    total: 12,
    updated_at: null,
  }));
  const historyPoint = history[1];
  const shippingProcessor = { ...processor, analytics_scope: "shipping" };
  const shippingSettings = { ...alwaysOnSettings, analytics_scope: "shipping", processors: [shippingProcessor] };
  const sync = { status: "synced", available: true, detail: "" };
  const shippingAnalytics = {
    ...analytics,
    analytics_scope: "shipping",
    analytics_sync: sync,
    total: 12,
    all_time_total: 24,
    model_all_time_total: 24,
    history,
    colors: historyPoint.colors,
    cameras: [
      {
        camera: "cam2",
        ...historyPoint,
        all_time_total: 24,
        history,
        dominant_color: "red",
        dominant_brand: null,
        analytics_sync: sync,
      },
    ],
  };
  mocks.responses.set("/cameras/monoblock-settings/", {
    camera_sources: ["cam2"],
    blocked_camera_sources: [],
    continuous_camera_sources: ["cam2"],
    continuous_source: "sub",
    continuous_sync_status: "synced",
    continuous_detail: "",
    camera_readiness: { cam2: { status: "synced", detail: "" } },
    locked: false,
    device_id: null,
    device_name: null,
    updated_at: null,
  });
  mocks.responses.set("/cameras/shipping-continuous-settings/", shippingSettings);
  mocks.responses.set("/cameras/shipping-continuous-analytics/", shippingAnalytics);
  mocks.responses.set("/cameras/always-on-settings/", { ...alwaysOnSettings, camera_sources: [], processors: [] });
  mocks.apiGet.mockClear();
  mocks.apiGet.mockImplementation((url: unknown, options?: { signal?: AbortSignal }) => {
    if (url === "/cameras/shipping-continuous-detections/")
      return Promise.resolve({ data: { processors: [shippingProcessor] } });
    if (url === "/cameras/shipping-continuous-settings/") return Promise.resolve({ data: shippingSettings });
    if (url === "/cameras/shipping-continuous-analytics/") return Promise.resolve({ data: shippingAnalytics });
    if (typeof url === "string" && url.startsWith("/cameras/shipping-continuous-history/?"))
      return getHistory(new URL(url, "http://localhost").searchParams.get("day") ?? "", options);
    return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
  });
}

async function openShippingDay(user: ReturnType<typeof userEvent.setup>, { range = false } = {}) {
  render(<MonoblockPage />);
  await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
  await user.click(screen.getByRole("tab", { name: "Аналитика" }));
  if (range) await user.click(screen.getByRole("button", { name: "7 дней" }));
  await user.click(
    screen.getByRole("button", {
      name: range ? "Аналитика за 24.08.2026: 12 мешков" : "Подсчёт по времени: 24.08.2026, 12 мешков",
    }),
  );
}

function selectedDayPanel(day: string) {
  const panel = screen.getByRole("heading", { name: day }).closest('[data-testid="always-on-panel"]');
  if (!(panel instanceof HTMLElement)) throw new Error("Карточка выбранного дня не найдена");
  return panel;
}

beforeEach(() => {
  mocks.requestedUrls = [];
  mocks.permissions = ["shipping.load"];
  mocks.isSuperuser = false;
  mocks.apiPut.mockReset();
  mocks.apiPost.mockReset();
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
  it("mounts number camera settings only in the shipping modal for a superuser", async () => {
    mocks.isSuperuser = true;
    setupShippingHistory();
    const user = userEvent.setup();
    render(<MonoblockPage />);
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    expect(screen.queryByTestId("transport-camera")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Камера номера" }));
    expect(screen.getByTestId("transport-camera")).toHaveTextContent("cam2");
    await user.click(screen.getByRole("tab", { name: "Аналитика" }));
    expect(screen.queryByTestId("transport-camera")).not.toBeInTheDocument();
  });

  it("does not expose number camera settings through the manage permission alone", async () => {
    mocks.permissions = ["shipping.load", "sys_permissions.manage"];
    setupShippingHistory();
    const user = userEvent.setup();
    render(<MonoblockPage />);
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    expect(screen.queryByRole("tab", { name: "Камера номера" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("transport-camera")).not.toBeInTheDocument();
  });

  it("не подменяет недоступный живой счётчик нулём", async () => {
    const user = userEvent.setup();
    const unavailableSettings = {
      ...alwaysOnSettings,
      processors: [{ ...processor, running: false, processor_alive: false, total: 0 }],
      camera_readiness: { cam2: { status: "pending", detail: "Процессор ещё не подтверждён" } },
    };
    mocks.responses.set("/cameras/always-on-settings/", unavailableSettings);
    mocks.apiGet.mockImplementation((url: unknown) => {
      if (url === "/cameras/always-on-detections/") return new Promise(() => undefined);
      if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: unavailableSettings });
      if (url === "/cameras/always-on-analytics/") return Promise.resolve({ data: analytics });
      return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
    });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));

    const label = screen.getByText("Текущий цикл");
    expect(label.parentElement).toHaveTextContent("Текущий цикл—");
    expect(label.parentElement).not.toHaveTextContent("Текущий цикл0");
  });

  it("показывает живой итог только для подтверждённого sub-процессора своего контура", async () => {
    const user = userEvent.setup();

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));

    const label = screen.getByText("Текущий цикл");
    expect(label.parentElement).toHaveTextContent("Текущий цикл17");
  });

  it("показывает периоды отгрузки и переключает алгоритм без запросов выпуска AI 24/7", async () => {
    const user = userEvent.setup();
    setupShippingHistory();
    await openShippingDay(user);

    const panel = selectedDayPanel("24.08.2026");
    const algorithmRun = await within(panel).findByRole("group", { name: "Период Красный: 12 мешков" });
    expect(algorithmRun).toHaveTextContent("09:00");
    expect(algorithmRun).toHaveTextContent("идёт сейчас");
    expect(within(panel).getByRole("group", { name: "Красный: 12 мешков" })).toHaveTextContent("100%");
    expect(within(panel).getByRole("button", { name: "Алгоритм" })).toHaveAttribute("aria-pressed", "true");
    expect(mocks.apiGet).toHaveBeenCalledWith(shippingHistoryUrl("2026-08-24"), {
      signal: expect.any(AbortSignal),
    });

    await user.click(within(panel).getByRole("button", { name: "Сырые данные" }));
    const firstRed = within(panel).getByRole("group", { name: "Период Красный: 4 мешков" });
    const blue = within(panel).getByRole("group", { name: "Период Синий: 3 мешков" });
    const lastRed = within(panel).getByRole("group", { name: "Период Красный: 5 мешков" });
    expect(firstRed).toHaveTextContent("09:00");
    expect(firstRed).toHaveTextContent("09:01");
    expect(blue).toHaveTextContent("09:02");
    expect(blue).toHaveTextContent("09:03");
    expect(lastRed).toHaveTextContent("09:04");
    expect(lastRed).toHaveTextContent("идёт сейчас");
    expect(within(panel).getAllByText("идёт сейчас")).toHaveLength(1);
    expect(within(panel).getByRole("group", { name: "Красный: 9 мешков" })).toHaveTextContent("75%");
    expect(within(panel).getByRole("group", { name: "Синий: 3 мешков" })).toHaveTextContent("25%");
    expect(within(panel).getByText("Учтено за день").parentElement).toHaveTextContent("12");
    expect(within(panel).queryByText("Цвета и продукция за день")).not.toBeInTheDocument();
    expect(panel.querySelector("[data-receipt-binding]")).toBeNull();
    expect(within(panel).queryByText(/Не привязан|Куда приходовать|Бренд недоступен/)).not.toBeInTheDocument();
    expect(mocks.requestedUrls.some((url) => url.startsWith("/cameras/always-on-production/"))).toBe(false);
    expect(mocks.apiPut).not.toHaveBeenCalled();
    expect(mocks.apiPost).not.toHaveBeenCalled();
  });

  it.each(["incomplete", "pending"] as const)(
    "объясняет %s историю отгрузки и позволяет обновить её без ложного пустого журнала",
    async (historyStatus) => {
      const user = userEvent.setup();
      const detail = "Для части мешков сохранён только общий итог, точное время неизвестно.";
      let ready = false;
      setupShippingHistory(async (day) => ({
        data: ready
          ? shippingDayHistory(day)
          : { ...shippingDayHistory(day), history_status: historyStatus, history_detail: detail, day_runs: [] },
      }));
      await openShippingDay(user);
      const panel = selectedDayPanel("24.08.2026");
      expect(await within(panel).findByText(detail)).toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "Алгоритм" })).toBeDisabled();
      expect(within(panel).getByRole("button", { name: "Сырые данные" })).toBeDisabled();
      expect(within(panel).queryByRole("group", { name: /^Период / })).not.toBeInTheDocument();
      expect(within(panel).queryByText("Детализация за 24.08.2026 недоступна.")).not.toBeInTheDocument();
      expect(within(panel).getByText("Учтено за день").parentElement).toHaveTextContent("12");
      expect(within(panel).getByRole("group", { name: "Красный: 9 мешков" })).toBeInTheDocument();

      ready = true;
      await user.click(within(panel).getByRole("button", { name: "Обновить" }));
      expect(await within(panel).findByRole("group", { name: "Период Красный: 12 мешков" })).toBeInTheDocument();
      expect(within(panel).queryByText(detail)).not.toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "Сырые данные" })).toBeEnabled();
    },
  );

  it("показывает ошибку запроса журнала отгрузки и повторяет только дневную детализацию", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    setupShippingHistory(async (day) => {
      attempts += 1;
      if (attempts === 1) throw new Error("Connection lost");
      return { data: shippingDayHistory(day) };
    });
    await openShippingDay(user);
    const panel = selectedDayPanel("24.08.2026");
    expect(await within(panel).findByRole("alert")).toHaveTextContent("Ошибка тестового API");
    expect(within(panel).getByRole("button", { name: "Алгоритм" })).toBeDisabled();
    expect(within(panel).queryByRole("group", { name: /^Период / })).not.toBeInTheDocument();
    expect(within(panel).getByText("Учтено за день").parentElement).toHaveTextContent("12");
    const beforeRetry = mocks.requestedUrls.length;
    await user.click(within(panel).getByRole("button", { name: "Повторить" }));
    expect(await within(panel).findByRole("group", { name: "Период Красный: 12 мешков" })).toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
    expect(attempts).toBe(2);
    expect(mocks.requestedUrls.slice(beforeRetry)).toEqual([shippingHistoryUrl("2026-08-24")]);
  });

  it("отменяет дневной запрос отгрузки и игнорирует поздний ответ после выбора другого дня", async () => {
    const user = userEvent.setup();
    let resolveOld: ((value: { data: ShippingCameraDayHistory }) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    setupShippingHistory((day, options) => {
      if (day === "2026-08-24") {
        firstSignal = options?.signal;
        return new Promise((resolve) => {
          resolveOld = resolve;
        });
      }
      return Promise.resolve({ data: shippingDayHistory(day) });
    });
    await openShippingDay(user, { range: true });
    expect(within(selectedDayPanel("24.08.2026")).getByText("Загружаем периоды дня…")).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(false);
    await user.click(screen.getByRole("button", { name: "Аналитика за 23.08.2026: 12 мешков" }));
    expect(firstSignal?.aborted).toBe(true);
    const panel = selectedDayPanel("23.08.2026");
    const run = await within(panel).findByRole("group", { name: "Период Красный: 12 мешков" });
    expect(run).toHaveTextContent("09:05");
    expect(within(panel).queryByText("идёт сейчас")).not.toBeInTheDocument();
    await act(async () => {
      resolveOld?.({ data: shippingDayHistory("2026-08-24") });
    });
    expect(within(panel).getByRole("group", { name: "Период Красный: 12 мешков" })).toBeInTheDocument();
    expect(within(panel).queryByText("идёт сейчас")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "24.08.2026" })).not.toBeInTheDocument();
  });

  it("отменяет дневной запрос при закрытии камеры и не возвращает старые периоды после открытия", async () => {
    const user = userEvent.setup();
    let resolveOld: ((value: { data: ShippingCameraDayHistory }) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    let attempts = 0;
    setupShippingHistory((day, options) => {
      attempts += 1;
      if (attempts === 1) {
        firstSignal = options?.signal;
        return new Promise((resolve) => {
          resolveOld = resolve;
        });
      }
      const current = shippingDayHistory(day);
      return Promise.resolve({
        data: {
          ...current,
          algorithm_day_runs: current.algorithm_day_runs?.map((run) => ({
            ...run,
            started_at: `${day}T11:00:00+05:00`,
            last_counted_at: `${day}T11:05:00+05:00`,
          })),
        },
      });
    });
    await openShippingDay(user);
    expect(firstSignal?.aborted).toBe(false);
    await user.keyboard("{Escape}");
    expect(firstSignal?.aborted).toBe(true);
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("tab", { name: "Аналитика" }));
    await user.click(screen.getByRole("button", { name: "Подсчёт по времени: 24.08.2026, 12 мешков" }));
    const panel = selectedDayPanel("24.08.2026");
    expect(await within(panel).findByRole("group", { name: "Период Красный: 12 мешков" })).toHaveTextContent("11:00");
    await act(async () => {
      resolveOld?.({ data: shippingDayHistory("2026-08-24") });
    });
    expect(within(panel).getByRole("group", { name: "Период Красный: 12 мешков" })).toHaveTextContent("11:00");
    expect(attempts).toBe(2);
  });

  it("обновляет периоды текущего дня и прекращает polling после закрытия детализации", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    let resolveFirst: ((value: { data: ShippingCameraDayHistory }) => void) | undefined;
    setupShippingHistory((day) => {
      attempts += 1;
      if (attempts === 1)
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      const current = shippingDayHistory(day);
      current.day_runs = current.day_runs.map((run) => ({ ...run, status: "closed", ended_at: run.last_counted_at }));
      current.algorithm_day_runs = current.algorithm_day_runs?.map((run) => ({
        ...run,
        status: "closed",
        ended_at: run.last_counted_at,
      }));
      return Promise.resolve({ data: current });
    });
    await openShippingDay(user);
    vi.useFakeTimers();
    try {
      await act(async () => {
        resolveFirst?.({ data: shippingDayHistory() });
      });
      const panel = selectedDayPanel("24.08.2026");
      expect(within(panel).getByText("идёт сейчас")).toBeInTheDocument();
      expect(attempts).toBe(1);
      await act(async () => {
        vi.advanceTimersByTime(15_000);
      });
      expect(attempts).toBe(2);
      expect(within(panel).queryByText("идёт сейчас")).not.toBeInTheDocument();
      expect(within(panel).getByRole("group", { name: "Период Красный: 12 мешков" })).toHaveTextContent("09:05");
      fireEvent.click(within(panel).getByRole("button", { name: "Закрыть" }));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(attempts).toBe(2);
      expect(screen.queryByRole("heading", { name: "24.08.2026" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    { bagsPresent: true, expected: "Мешки в кадре: есть" },
    { bagsPresent: false, expected: "Мешки в кадре: нет" },
    { bagsPresent: undefined, expected: "Мешки в кадре: нет данных" },
  ])("показывает tri-state наличие мешков: $expected", async ({ bagsPresent, expected }) => {
    const user = userEvent.setup();
    const nextProcessor = {
      ...processor,
      ...(bagsPresent === undefined ? {} : { bags_present: bagsPresent }),
    };
    mocks.responses.set("/cameras/always-on-settings/", {
      ...alwaysOnSettings,
      processors: [nextProcessor],
    });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));

    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.getByText("считает 24/7")).toBeInTheDocument();
  });

  it("обновляет наличие мешков из быстрого снимка детекций", async () => {
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    expect(screen.getByText("Мешки в кадре: нет данных")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await act(async () => {
      mocks.resolveDetections?.({
        data: { processors: [{ ...processor, bags_present: true }] },
      });
    });

    await waitFor(() => expect(screen.getByText("Мешки в кадре: есть")).toBeInTheDocument());
  });

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
          analytics_sync: { available: true, status: "synced", detail: "" },
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
    expect(screen.getByText("Продукция")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Уменьшить/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сдать в архив/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Архив" })).not.toBeInTheDocument();
  });

  it("не позволяет запоздавшему GET перезаписать сохранённые привязки", async () => {
    const user = userEvent.setup();
    mocks.permissions = ["shipping.load", "ai_247.manage"];
    const initialProduction = {
      camera: "cam2",
      warehouse: 1,
      warehouse_name: "Основной склад",
      warehouses: [{ id: 1, code: "main", name: "Основной склад", is_active: true, is_default: true }],
      timezone: "Asia/Almaty",
      close_time: "19:00",
      current_business_day: "2026-08-24",
      next_run_at: "2026-08-24T13:00:00Z",
      selected_day: null,
      day_runs: [],
      fully_configured: false,
      available_colors: ["red", "blue"],
      mappings: [
        { color: "red", product: 1, product_label: "Красная мука · 50 кг" },
        { color: "blue", product: null, product_label: null },
      ],
      products: [
        {
          id: 1,
          label: "Красная мука · 50 кг",
          color: "Red",
          color_label: "Красный",
          weight_kg: "50.00",
          warehouse: 1,
        },
        {
          id: 2,
          label: "Синяя мука · 50 кг",
          color: "Blue",
          color_label: "Синий",
          weight_kg: "50.00",
          warehouse: 1,
        },
      ],
      runs: [],
      preview: [],
      batches: [],
    };
    const savedProduction = {
      ...initialProduction,
      fully_configured: true,
      mappings: [initialProduction.mappings[0], { color: "blue", product: 2, product_label: "Синяя мука · 50 кг" }],
    };
    let resolveStale: ((value: { data: typeof initialProduction }) => void) | null = null;
    const staleResponse = new Promise<{ data: typeof initialProduction }>((resolve) => {
      resolveStale = resolve;
    });
    let productionGets = 0;
    mocks.apiGet.mockImplementation((url: unknown) => {
      if (url === "/cameras/always-on-detections/") return new Promise(() => undefined);
      if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: alwaysOnSettings });
      if (url === "/cameras/always-on-analytics/") return Promise.resolve({ data: analytics });
      if (url === "/cameras/always-on-production/?camera=cam2") {
        productionGets += 1;
        return productionGets === 1 ? Promise.resolve({ data: initialProduction }) : staleResponse;
      }
      return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
    });
    mocks.apiPut.mockResolvedValue({ data: savedProduction });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("tab", { name: "Выпуск и склад" }));
    await screen.findByLabelText("Товар для цвета Синий");

    await user.click(screen.getByRole("tab", { name: "Прямой эфир" }));
    await user.click(screen.getByRole("tab", { name: "Выпуск и склад" }));
    await waitFor(() => expect(productionGets).toBe(2));
    await user.selectOptions(screen.getByLabelText("Товар для цвета Синий"), "2");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.getByText("всё готово")).toBeInTheDocument());

    await act(async () => {
      resolveStale?.({ data: initialProduction });
    });
    expect(screen.getByText("всё готово")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Развернуть настройку прихода" }));
    expect(screen.getByLabelText("Товар для цвета Синий")).toHaveValue("2");
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
          analytics_sync: { available: true, status: "synced", detail: "" },
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
      warehouse: 2,
      warehouse_name: "Склад готовой продукции",
      products: [
        {
          id: 1,
          label: "ДБН 1с 50кг · Красный 50 кг",
          color: "Red",
          color_label: "Красный",
          weight_kg: "50.00",
          warehouse: 2,
        },
        {
          id: 3,
          label: "ДБН вс 50кг · Синий 50 кг",
          color: "Blue",
          color_label: "Синий",
          weight_kg: "50.00",
          warehouse: 2,
        },
      ],
      dominant_brand_by_color: {
        red: "dikhan_baba",
        green: "korol",
        blue: "korol",
      },
      mappings: [
        { color: "red", product: 1, product_label: "ДБН 1с 50кг · Красный 50 кг" },
        { color: "green", product: null, product_label: null },
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
    const currentProductionUrl = "/cameras/always-on-production/?camera=cam2";
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
      if (url === currentProductionUrl) return Promise.resolve({ data: { ...productionDay, selected_day: null } });
      if (url === productionUrl) return Promise.resolve({ data: productionDay });
      if (url === legacyProductionUrl) return Promise.resolve({ data: legacyProductionDay });
      if (url === archivedProductionUrl) return Promise.resolve({ data: archivedProductionDay });
      return Promise.reject(new Error(`Unexpected GET ${String(url)}`));
    });

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
    await user.click(screen.getByRole("tab", { name: "Аналитика" }));
    await user.click(screen.getByRole("button", { name: "30 дней" }));
    await waitFor(() =>
      expect(mocks.apiGet).toHaveBeenCalledWith(currentProductionUrl, {
        signal: expect.any(AbortSignal),
      }),
    );

    const allTimeColorsPanel = screen.getByText("Продукция").closest('[data-testid="always-on-panel"]');
    if (!(allTimeColorsPanel instanceof HTMLElement)) throw new Error("Общая карточка цветов не найдена");
    expect(within(allTimeColorsPanel).getByText("ДБН 1с 50кг · Красный 50 кг")).toBeInTheDocument();
    const allTimeRedBinding = allTimeColorsPanel.querySelector('[data-receipt-binding="bound"]');
    expect(allTimeRedBinding).toHaveTextContent("Склад готовой продукции");
    expect(within(allTimeColorsPanel).queryByText("Красный")).not.toBeInTheDocument();
    expect(allTimeColorsPanel.getElementsByClassName("bg-[#dc604d]")).toHaveLength(2);
    expect(within(allTimeColorsPanel).queryByText("Синий")).not.toBeInTheDocument();
    expect(within(allTimeColorsPanel).getByText("Зелёный")).toBeInTheDocument();
    expect(allTimeColorsPanel.querySelector('[data-receipt-binding="unbound"]')).toHaveTextContent("Не привязан");

    await user.click(screen.getByRole("button", { name: "Аналитика за 24.08.2026: 153 мешков" }));

    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledWith(productionUrl, { signal: expect.any(AbortSignal) }));
    const heading = screen.getByRole("heading", { name: "24.08.2026" });
    const dayPanel = heading.closest('[data-testid="always-on-panel"]');
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
    const algorithmRed = await within(dayPanel).findByRole("group", {
      name: "ДБН 1с 50кг · Красный 50 кг: 153 мешков",
    });
    expect(within(algorithmRed).getByText("96.8%")).toBeInTheDocument();
    const redMapping = within(algorithmRed)
      .getByText("ДБН 1с 50кг · Красный 50 кг")
      .closest('[data-receipt-binding="bound"]');
    if (!(redMapping instanceof HTMLElement)) throw new Error("Привязка красного цвета не найдена");
    expect(redMapping).toHaveTextContent("Склад готовой продукции");
    const redBrand = within(algorithmRed).getByText("Бренд: Дихан Баба");
    expect(redMapping.compareDocumentPosition(redBrand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(algorithmRed).queryByText("Красный")).not.toBeInTheDocument();
    expect(algorithmRed.getElementsByClassName("bg-[#dc604d]")).toHaveLength(1);
    expect(algorithmRed.getElementsByClassName("size-2.5")).toHaveLength(1);
    expect(redBrand.previousElementSibling).toHaveClass("bg-[#dc604d]");
    const algorithmGreen = within(dayPanel).getByRole("group", { name: "Зелёный: 5 мешков" });
    const greenBinding = algorithmGreen.querySelector('[data-receipt-binding="unbound"]');
    expect(greenBinding).toHaveTextContent("Не привязан");
    expect(greenBinding).toHaveClass("text-red-700");
    expect(within(algorithmGreen).getByText("Зелёный · Korol")).toBeInTheDocument();
    expect(within(dayPanel).queryByRole("group", { name: /Синий: / })).not.toBeInTheDocument();
    expect(within(dayPanel).getAllByText("меш.")).toHaveLength(2);
    expect(within(dayPanel).queryByText("Korol")).not.toBeInTheDocument();
    expect(within(dayPanel).queryByText("Бренды за день")).not.toBeInTheDocument();

    await user.click(within(dayPanel).getByRole("button", { name: "Сырые данные" }));

    expect(within(dayPanel).getByRole("button", { name: "Сырые данные" })).toHaveAttribute("aria-pressed", "true");
    const rawRed = within(dayPanel).getByRole("group", {
      name: "ДБН 1с 50кг · Красный 50 кг: 150 мешков",
    });
    expect(within(rawRed).getByText("94.9%")).toBeInTheDocument();
    expect(within(rawRed).getByText("Бренд: Дихан Баба")).toBeInTheDocument();
    expect(within(rawRed).queryByText("Красный")).not.toBeInTheDocument();
    expect(rawRed.getElementsByClassName("bg-[#dc604d]")).toHaveLength(1);
    expect(within(dayPanel).getByRole("group", { name: "Зелёный: 5 мешков" })).toBeInTheDocument();
    const rawBlue = within(dayPanel).getByRole("group", {
      name: "ДБН вс 50кг · Синий 50 кг: 3 мешков",
    });
    expect(within(rawBlue).getByText("ДБН вс 50кг · Синий 50 кг")).toBeInTheDocument();
    const rawBlueBrand = within(rawBlue).getByText("Бренд: Korol");
    expect(rawBlueBrand).toBeInTheDocument();
    expect(rawBlueBrand.previousElementSibling).toHaveClass("bg-[#4169d8]");
    expect(rawBlue.getElementsByClassName("size-2.5")).toHaveLength(1);
    expect(within(rawBlue).queryByText("Синий")).not.toBeInTheDocument();
    expect(within(dayPanel).getAllByText("меш.")).toHaveLength(4);
    expect(within(dayPanel).queryByText("Korol")).not.toBeInTheDocument();

    // Предыдущий API не знает об algorithm_day_runs/run_smoothing: оба
    // режима должны без ошибки показать исходный дневной срез.
    await user.click(screen.getByRole("button", { name: "Аналитика за 23.08.2026: 12 мешков" }));
    await waitFor(() =>
      expect(mocks.apiGet).toHaveBeenCalledWith(legacyProductionUrl, { signal: expect.any(AbortSignal) }),
    );
    const legacyHeading = screen.getByRole("heading", { name: "23.08.2026" });
    const legacyDayPanel = legacyHeading.closest('[data-testid="always-on-panel"]');
    if (!(legacyDayPanel instanceof HTMLElement)) throw new Error("Карточка legacy-дня не найдена");
    await waitFor(() =>
      expect(within(legacyDayPanel).getByRole("button", { name: "Алгоритм" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(
      within(legacyDayPanel).getByRole("group", { name: "ДБН 1с 50кг · Красный 50 кг: 10 мешков" }),
    ).toBeInTheDocument();
    expect(
      within(legacyDayPanel).getByRole("group", { name: "ДБН вс 50кг · Синий 50 кг: 2 мешков" }),
    ).toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("ДБН 1с 50кг · Красный 50 кг")).toHaveLength(2);
    expect(within(legacyDayPanel).getAllByText("ДБН вс 50кг · Синий 50 кг")).toHaveLength(2);
    expect(within(legacyDayPanel).getAllByText(/Бренд недоступен$/)).toHaveLength(2);
    expect(within(legacyDayPanel).queryByText("Сопоставление недоступно")).not.toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("меш.")).toHaveLength(2);
    expect(allTimeColorsPanel.querySelector('[data-receipt-binding="bound"]')).toHaveTextContent(
      "Склад готовой продукции",
    );

    await user.click(within(legacyDayPanel).getByRole("button", { name: "Сырые данные" }));
    expect(
      within(legacyDayPanel).getByRole("group", { name: "ДБН 1с 50кг · Красный 50 кг: 10 мешков" }),
    ).toBeInTheDocument();
    expect(
      within(legacyDayPanel).getByRole("group", { name: "ДБН вс 50кг · Синий 50 кг: 2 мешков" }),
    ).toBeInTheDocument();
    expect(within(legacyDayPanel).getAllByText("меш.")).toHaveLength(2);

    // Append-only production runs include the part already moved to an
    // archive. Never mix that full ledger (140) with the active slice (40).
    await user.click(screen.getByRole("button", { name: "Аналитика за 22.08.2026: 40 мешков" }));
    await waitFor(() =>
      expect(mocks.apiGet).toHaveBeenCalledWith(archivedProductionUrl, { signal: expect.any(AbortSignal) }),
    );
    const archivedHeading = screen.getByRole("heading", { name: "22.08.2026" });
    const archivedDayPanel = archivedHeading.closest('[data-testid="always-on-panel"]');
    if (!(archivedDayPanel instanceof HTMLElement)) throw new Error("Карточка архивного среза не найдена");
    expect(
      await within(archivedDayPanel).findByRole("group", { name: "ДБН вс 50кг · Синий 50 кг: 40 мешков" }),
    ).toBeInTheDocument();
    expect(within(archivedDayPanel).queryByRole("group", { name: /ДБН 1с 50кг/ })).not.toBeInTheDocument();
    expect(within(archivedDayPanel).getByRole("status")).toHaveTextContent(
      "журнал не совпадает с итогом выбранного дня",
    );
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

it("requests today by default and replaces the analytics period without accepting an old response", async () => {
  const user = userEvent.setup();
  let resolveOld: ((value: { data: unknown }) => void) | undefined;
  const old = new Promise<{ data: unknown }>((resolve) => {
    resolveOld = resolve;
  });
  const snapshot = (total: number) => ({
    ...analytics,
    cameras: [
      {
        camera: "cam2",
        day: "2026-08-24",
        total,
        all_time_total: total,
        colors: [],
        history: [],
        analytics_sync: { available: true, status: "synced", detail: "" },
      },
    ],
  });
  let reads = 0;
  mocks.apiGet.mockImplementation((url: string) => {
    if (url === "/cameras/always-on-settings/") return Promise.resolve({ data: alwaysOnSettings });
    if (url === "/cameras/always-on-analytics/") return ++reads === 1 ? old : Promise.resolve({ data: snapshot(140) });
    if (url.includes("detections")) return Promise.resolve({ data: { processors: [] } });
    return Promise.reject(new Error("Нет данных выпуска"));
  });
  render(<MonoblockPage />);
  await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
  await user.click(screen.getByRole("button", { name: "Открыть прямой эфир камеры Робот Кука" }));
  await user.click(screen.getByRole("tab", { name: "Аналитика" }));
  expect(screen.getByLabelText("Аналитика с даты")).toHaveValue("2026-08-24");
  expect(mocks.requestedUrls).toContain(
    "/cameras/always-on-analytics/?camera=cam2&date_from=2026-08-24&date_to=2026-08-24",
  );
  fireEvent.change(screen.getByLabelText("Аналитика с даты"), { target: { value: "2026-08-01" } });
  await waitFor(() =>
    expect(mocks.requestedUrls).toContain(
      "/cameras/always-on-analytics/?camera=cam2&date_from=2026-08-01&date_to=2026-08-24",
    ),
  );
  const periodPanel = screen.getByText("За выбранный период").parentElement;
  if (!periodPanel) throw new Error("Нет карточки периода");
  await waitFor(() => expect(within(periodPanel).getByText("140")).toBeInTheDocument());
  await act(async () => {
    resolveOld?.({ data: snapshot(999) });
  });
  expect(screen.queryByText("999")).not.toBeInTheDocument();
  const beforeInvalid = mocks.requestedUrls.length;
  fireEvent.change(screen.getByLabelText("Аналитика с даты"), { target: { value: "2026-08-25" } });
  expect(screen.getByRole("alert")).toHaveTextContent("Выберите период");
  expect(mocks.requestedUrls.length).toBe(beforeInvalid);
  await user.click(screen.getByRole("button", { name: "Сегодня" }));
  expect(screen.getByLabelText("Аналитика с даты")).toHaveValue("2026-08-24");
});
