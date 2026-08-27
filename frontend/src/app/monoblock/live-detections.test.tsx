import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MonoblockPage from "./page";

const mocks = vi.hoisted(() => ({
  responses: new Map<string, unknown>(),
  apiGet: vi.fn(),
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
      permissions: ["shipping.load"],
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
  it("показывает бренд-разбивку и использует основной бренд из ответа сервера", async () => {
    const user = userEvent.setup();
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

    const dominantBrandPanel = screen.getByText("Основной бренд").parentElement as HTMLElement;
    expect(within(dominantBrandPanel).getByText("Korol")).toBeInTheDocument();
    expect(within(dominantBrandPanel).queryByText("Future Brand")).not.toBeInTheDocument();
    expect(screen.getByText("Future Brand")).toBeInTheDocument();
    expect(screen.getByText("Дихан Баба")).toBeInTheDocument();
    expect(screen.getByText("Не распознано")).toBeInTheDocument();
    expect(screen.getByText("Нет данных (старые)")).toBeInTheDocument();
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
