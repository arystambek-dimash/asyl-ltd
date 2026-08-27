import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AiStatus } from "@/lib/use-ai-counter";
import MonoblockPage from "./page";

const mocks = vi.hoisted(() => ({
  aiStatus: null as AiStatus | null,
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: {
      id: 1,
      username: "monoblock-cam2",
      is_client: false,
      is_superuser: false,
      is_monoblock: true,
      monoblock_name: "Моноблок 2",
      monoblock_camera: "cam2",
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
  CameraStream: ({ src, onStateChange }: { src: string; onStateChange?: (online: boolean) => void }) => (
    <button type="button" data-testid="active-session-stream" data-src={src} onClick={() => onStateChange?.(true)}>
      Подключить тестовый поток
    </button>
  ),
  ensureCameraStreamToken: vi.fn(),
}));

vi.mock("@/components/detection-overlay", () => ({
  DetectionOverlay: ({
    detections,
    frame,
  }: {
    detections?: Array<{ label?: string; class_name?: string }>;
    frame?: { width?: number; height?: number } | null;
  }) => (
    <div data-testid="active-session-detections" data-frame={`${frame?.width}x${frame?.height}`}>
      {(detections ?? []).map((detection) => detection.label ?? detection.class_name).join(",")}
    </div>
  ),
}));

vi.mock("@/components/camera-counting-line-overlay", () => ({
  CameraCountingLineOverlay: ({
    line,
    direction,
  }: {
    line: { x1: number; y1: number; x2: number; y2: number };
    direction: string;
  }) => (
    <div
      data-testid="active-session-line"
      data-line={`${line.x1},${line.y1},${line.x2},${line.y2}`}
      data-direction={direction}
    />
  ),
}));

vi.mock("@/lib/use-ai-counter", () => ({
  useAiCounter: () => ({
    status: mocks.aiStatus,
    running: !!mocks.aiStatus?.running,
    occupied: false,
    busy: false,
    stale: false,
    error: "",
    orderId: 404,
    start: vi.fn(),
    stop: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: () => undefined,
}));

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    let data: unknown = null;
    if (url === "/orders/?post_board=1") data = [];
    if (url === "/cameras/") {
      data = [
        {
          id: "nvr:cam2",
          name: "cam2",
          zone: "Конвейер вагон",
          src: "cam2",
          kind: "nvr-channel",
          online: true,
          line_config: {
            configured: true,
            line: { x1: 0.2, y1: 0.3, x2: 0.8, y2: 0.3 },
            direction: "down",
          },
        },
      ];
    }
    if (url === "/cameras/ai/sessions/") {
      data = [
        {
          id: 100,
          order_id: 404,
          order_client_name: "Мурат Дг",
          order_truck_number: "",
          camera: "cam2",
          status: "active",
          started_at: "2026-08-27T05:20:24Z",
          started_by_id: 1,
          started_by_name: "loader@example.com",
          can_stop: true,
          last_status: { total: 0 },
        },
      ];
    }
    if (url === "/cameras/monoblock-settings/") {
      data = {
        camera_sources: ["cam2"],
        locked: true,
        device_id: 1,
        device_name: "Моноблок 2",
        updated_at: null,
      };
    }
    if (url === "/cameras/monoblock-devices/") data = [];
    return {
      data,
      error: "",
      loading: false,
      reload: vi.fn().mockResolvedValue(undefined),
      setData: vi.fn(),
    };
  },
}));

beforeEach(() => {
  mocks.aiStatus = {
    running: true,
    stream: "cam2ai",
    total: 0,
    detections: [{ bbox: [64, 72, 256, 288], class_name: "Blue_50", confidence: 0.82, counted: false }],
    detection_frame: { width: 640, height: 360 },
    line: "0.1,0.4,0.9,0.6",
    direction: "negative",
    last_frame_at: "2026-08-27T06:35:57.449Z",
  };
});

describe("активная AI-отгрузка", () => {
  it("показывает базовый поток и рисует рамки с живой линией поверх него", async () => {
    const user = userEvent.setup();
    render(<MonoblockPage />);

    const stream = screen.getByTestId("active-session-stream");
    expect(stream).toHaveAttribute("data-src", "cam2");
    expect(stream).not.toHaveAttribute("data-src", "cam2ai");
    expect(screen.getByText("ПОДКЛЮЧЕНИЕ ВИДЕО")).toBeInTheDocument();
    expect(screen.queryByTestId("active-session-detections")).not.toBeInTheDocument();

    await user.click(stream);

    expect(screen.getByText("СЧИТЫВАНИЕ")).toBeInTheDocument();
    expect(screen.getByTestId("active-session-detections")).toHaveTextContent("Blue_50");
    expect(screen.getByTestId("active-session-detections")).toHaveAttribute("data-frame", "640x360");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-line", "0.1,0.4,0.9,0.6");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-direction", "negative");
  });

  it("использует сохранённую линию камеры, если процессор ещё не прислал живую", async () => {
    mocks.aiStatus = { running: true, stream: "cam2ai", total: 0 };
    const user = userEvent.setup();
    render(<MonoblockPage />);

    await user.click(screen.getByTestId("active-session-stream"));

    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-line", "0.2,0.3,0.8,0.3");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-direction", "down");
  });
});
