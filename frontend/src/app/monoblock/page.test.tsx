import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/lib/types";
import MonoblockPage from "./page";

const mocks = vi.hoisted(() => ({
  me: null as Me | null,
  urls: [] as (string | null)[],
  cameras: [] as Array<Record<string, unknown>>,
  alwaysOnSources: [] as string[],
  shippingSources: [] as string[],
  analyticsAvailable: true,
  aiAnalyticsAvailable: true,
  analyticsSyncPresent: true,
  analyticsApiError: "",
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: mocks.me, loading: false }),
}));

vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/shipping/shipment-launcher", () => ({
  ShipmentLauncher: () => <div>Запуск отгрузки</div>,
}));

vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: () => undefined,
}));

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.urls.push(url);
    let data: unknown = null;
    if (url === "/orders/?post_board=1" || url === "/cameras/ai/sessions/") data = [];
    if (url === "/cameras/") data = mocks.cameras;
    if (url === "/cameras/monoblock-settings/") {
      data = {
        camera_sources: mocks.shippingSources,
        blocked_camera_sources: mocks.alwaysOnSources,
        continuous_camera_sources: mocks.shippingSources,
        continuous_source: "sub",
        continuous_sync_status: "synced",
        continuous_detail: "",
        camera_readiness: Object.fromEntries(
          mocks.shippingSources.map((source) => [source, { status: "synced", detail: "" }]),
        ),
        locked: false,
        device_id: null,
        device_name: null,
        updated_at: null,
      };
    }
    if (url === "/cameras/always-on-settings/") {
      data = {
        camera_sources: mocks.alwaysOnSources,
        analytics_scope: "ai_247",
        automatic_camera_sources: [],
        manual_camera_sources: mocks.alwaysOnSources,
        blocked_camera_sources: mocks.shippingSources,
        source: "sub",
        processors: [],
        capacity: null,
        service_available: true,
        sync_status: "synced",
        detail: "",
        camera_readiness: {},
        updated_at: null,
      };
    }
    if (url === "/cameras/shipping-continuous-settings/") {
      data = {
        camera_sources: mocks.shippingSources,
        analytics_scope: "shipping",
        blocked_camera_sources: mocks.alwaysOnSources,
        source: "sub",
        processors: mocks.shippingSources.map((cam) => ({
          cam,
          running: true,
          mode: "always_on",
          recording: false,
          total: 12,
          analytics_scope: "shipping",
        })),
        capacity: null,
        service_available: true,
        sync_status: "synced",
        detail: "",
        camera_readiness: Object.fromEntries(
          mocks.shippingSources.map((source) => [source, { status: "synced", detail: "" }]),
        ),
        updated_at: null,
      };
    }
    if (url === "/cameras/always-on-analytics/") {
      data = {
        day: "2026-08-17",
        total: 0,
        all_time_total: 0,
        model_all_time_total: 0,
        adjustment: 0,
        history: [],
        colors: [],
        dominant_color: null,
        analytics_scope: "ai_247",
        ...(mocks.analyticsSyncPresent
          ? {
              analytics_sync: {
                status: mocks.aiAnalyticsAvailable ? "synced" : "error",
                available: mocks.aiAnalyticsAvailable,
                detail: mocks.aiAnalyticsAvailable ? "" : "Журнал событий недоступен",
              },
            }
          : {}),
        cameras: [],
      };
    }
    if (url === "/cameras/shipping-continuous-analytics/") {
      data = {
        day: "2026-09-01",
        total: mocks.shippingSources.length ? 12 : 0,
        all_time_total: mocks.shippingSources.length ? 12 : 0,
        model_all_time_total: mocks.shippingSources.length ? 12 : 0,
        adjustment: 0,
        history: [],
        colors: [],
        dominant_color: null,
        model_per_brand: {},
        brands: [],
        dominant_brand: null,
        analytics_scope: "shipping",
        ...(mocks.analyticsSyncPresent
          ? {
              analytics_sync: {
                status: mocks.analyticsAvailable ? "synced" : "error",
                available: mocks.analyticsAvailable,
                detail: mocks.analyticsAvailable ? "" : "Журнал событий недоступен",
              },
            }
          : {}),
        cameras: mocks.shippingSources.map((camera) => ({
          camera,
          day: "2026-09-01",
          model_total: 12,
          model_per_color: {},
          model_per_brand: {},
          adjustment: 0,
          total: 12,
          all_time_total: 12,
          history: [],
          colors: [],
          brands: [],
          dominant_color: null,
          dominant_brand: null,
          updated_at: null,
          ...(mocks.analyticsSyncPresent
            ? {
                analytics_sync: {
                  status: mocks.analyticsAvailable ? "synced" : "error",
                  available: mocks.analyticsAvailable,
                  detail: mocks.analyticsAvailable ? "" : "Журнал событий недоступен",
                },
              }
            : {}),
        })),
      };
    }
    const isAnalyticsRequest = url?.endsWith("-analytics/") ?? false;
    return {
      data,
      error: isAnalyticsRequest ? mocks.analyticsApiError : "",
      loading: false,
      reload: vi.fn().mockResolvedValue(undefined),
      setData: vi.fn(),
    };
  },
}));

const employee: Me = {
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
};

beforeEach(() => {
  mocks.me = employee;
  mocks.urls = [];
  mocks.cameras = [];
  mocks.alwaysOnSources = [];
  mocks.shippingSources = [];
  mocks.analyticsAvailable = true;
  mocks.aiAnalyticsAvailable = true;
  mocks.analyticsSyncPresent = true;
  mocks.analyticsApiError = "";
});

describe("доступ к AI 24/7 на странице моноблока", () => {
  it("показывает мониторинг и загружает его данные обычному сотруднику", async () => {
    const user = userEvent.setup();
    render(<MonoblockPage />);

    const aiTab = screen.getByRole("tab", { name: /AI 24\/7/ });
    expect(aiTab).toBeInTheDocument();
    expect(mocks.urls).toContain("/cameras/always-on-settings/");
    expect(mocks.urls).toContain("/cameras/always-on-analytics/");

    await user.click(aiTab);
    expect(screen.getByText("Бесконечный цикл пока не запущен")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Настроить/ })).not.toBeInTheDocument();
  });

  it("показывает настройку только с правом ai_247.manage", async () => {
    mocks.me = { ...employee, permissions: ["shipping.load", "ai_247.manage"] };
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    expect(screen.getByRole("button", { name: /Настроить/ })).toBeInTheDocument();
  });

  it("не даёт добавить камеру отгрузки в отдельный контур AI 24/7", async () => {
    mocks.me = { ...employee, permissions: ["shipping.load", "ai_247.manage"] };
    mocks.cameras = [
      {
        id: "camera-2",
        name: "Camera 2",
        zone: "Пост погрузки",
        src: "cam2",
        kind: "direct",
        online: true,
      },
    ];
    mocks.shippingSources = ["cam2"];
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: /Настроить/ }));

    expect(
      screen.getByRole("button", {
        name: "Пост погрузки: принадлежит контуру отгрузки",
      }),
    ).toBeDisabled();
    expect(screen.getByText(/Камера отгрузки · cam2\/sub/)).toBeInTheDocument();
  });

  it("показывает непрерывную камеру отгрузки только во вкладке Отгрузки", async () => {
    mocks.cameras = [
      { id: "camera-2", name: "Camera 2", zone: "Пост погрузки", src: "cam2", kind: "direct", online: true },
    ];
    mocks.shippingSources = ["cam2"];
    const user = userEvent.setup();
    render(<MonoblockPage />);

    expect(screen.getByText("Камеры отгрузки · работают 24/7")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть прямой эфир камеры Пост погрузки" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    expect(screen.getByText("Бесконечный цикл пока не запущен")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть прямой эфир камеры Пост погрузки" })).not.toBeInTheDocument();
  });

  it("не выдаёт несинхронизированную аналитику отгрузки за честный ноль", () => {
    mocks.cameras = [
      { id: "camera-2", name: "Camera 2", zone: "Пост погрузки", src: "cam2", kind: "direct", online: true },
    ];
    mocks.shippingSources = ["cam2"];
    mocks.analyticsAvailable = false;

    render(<MonoblockPage />);

    expect(screen.getByText("Сегодня: —")).toBeInTheDocument();
    expect(screen.queryByText("Сегодня: 0")).not.toBeInTheDocument();
  });

  it("считает отсутствующий sync-контракт недоступной аналитикой", () => {
    mocks.cameras = [
      { id: "camera-2", name: "Camera 2", zone: "Пост погрузки", src: "cam2", kind: "direct", online: true },
    ];
    mocks.shippingSources = ["cam2"];
    mocks.analyticsSyncPresent = false;

    render(<MonoblockPage />);

    expect(screen.getByText("Сегодня: —")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть прямой эфир камеры Пост погрузки" })).toHaveTextContent("—");
  });

  it("не выдаёт несинхронизированную аналитику AI 24/7 за честный ноль", async () => {
    mocks.cameras = [
      { id: "camera-3", name: "Camera 3", zone: "Робот Кука", src: "cam3", kind: "direct", online: true },
    ];
    mocks.alwaysOnSources = ["cam3"];
    mocks.aiAnalyticsAvailable = false;
    const user = userEvent.setup();

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));

    expect(screen.getByText(/Сегодня:\s*—/)).toBeInTheDocument();
    expect(screen.getAllByText(/Всего:\s*—/)).not.toHaveLength(0);
  });

  it("при ошибке analytics API показывает прочерк даже при старом успешном payload", async () => {
    mocks.cameras = [
      { id: "camera-3", name: "Camera 3", zone: "Робот Кука", src: "cam3", kind: "direct", online: true },
    ];
    mocks.alwaysOnSources = ["cam3"];
    mocks.analyticsApiError = "camera-monitor недоступен";
    const user = userEvent.setup();

    render(<MonoblockPage />);
    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));

    expect(screen.getByText(/Сегодня:\s*—/)).toBeInTheDocument();
    expect(screen.getAllByText(/Всего:\s*—/)).not.toHaveLength(0);
  });

  it("не показывает вкладку и не запрашивает мониторинг техническому моноблоку", () => {
    mocks.me = {
      ...employee,
      username: "monoblock-cam2",
      is_monoblock: true,
      monoblock_name: "Моноблок 2",
      monoblock_camera: "cam2",
    };
    render(<MonoblockPage />);

    expect(screen.queryByRole("tab", { name: /AI 24\/7/ })).not.toBeInTheDocument();
    expect(mocks.urls).not.toContain("/cameras/always-on-settings/");
    expect(mocks.urls).not.toContain("/cameras/always-on-analytics/");
    expect(mocks.urls).toContain("/cameras/shipping-continuous-settings/");
    expect(mocks.urls).toContain("/cameras/shipping-continuous-analytics/");
  });
});
