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
  automaticSources: [] as string[],
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
        camera_sources: [],
        always_on_camera_sources: [],
        always_on_source: "sub",
        always_on_sync_status: "synced",
        always_on_detail: "",
        locked: false,
        device_id: null,
        device_name: null,
        updated_at: null,
      };
    }
    if (url === "/cameras/always-on-settings/") {
      data = {
        camera_sources: mocks.alwaysOnSources,
        automatic_camera_sources: mocks.automaticSources,
        manual_camera_sources: [],
        source: "sub",
        processors: [],
        capacity: null,
        service_available: true,
        sync_status: "synced",
        detail: "",
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
        cameras: [],
      };
    }
    return {
      data,
      error: "",
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
  mocks.automaticSources = [];
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

  it("показывает камеру Моноблока как автоматическую и не даёт снять её вручную", async () => {
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
    mocks.alwaysOnSources = ["cam2"];
    mocks.automaticSources = ["cam2"];
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByRole("tab", { name: /AI 24\/7/ }));
    await user.click(screen.getByRole("button", { name: /Настроить/ }));

    expect(
      screen.getByRole("button", {
        name: "Пост погрузки: автоматически включена Моноблоком",
      }),
    ).toBeDisabled();
    expect(screen.getByText(/Автоматически · Моноблок · cam2\/sub/)).toBeInTheDocument();
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
  });
});
