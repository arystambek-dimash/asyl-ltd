import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CameraFeed } from "@/components/camera-wall";
import type { BagCounterHandle } from "@/components/shipping/bag-counter";
import { ShippingRowDetail } from "@/components/shipping/shipping-row-detail";
import type { AiCountingSession, Order } from "@/lib/types";
import type { AiStatus } from "@/lib/use-ai-counter";

const mocks = vi.hoisted(() => ({
  aiStatus: null as AiStatus | null,
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

const camera: CameraFeed = {
  id: "nvr:cam2",
  name: "cam2",
  zone: "Конвейер вагон",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
  line_config: {
    configured: true,
    coordinate_space: "normalized",
    line: { x1: 0.2, y1: 0.3, x2: 0.8, y2: 0.3 },
    direction: "down",
  },
};

const session: AiCountingSession = {
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
};

const order: Order = {
  id: 404,
  client: 1,
  client_name: "Мурат Дг",
  currency: "KZT",
  status: "loading",
  transport_type: "truck",
  truck_number: "",
  items: [],
  total_amount: "0.00",
  paid_total: "0.00",
  is_fully_paid: true,
  debt_override: false,
  created_at: "2026-08-27T00:00:00Z",
  loading_camera: "cam2",
};

function renderDetail() {
  render(
    <ShippingRowDetail
      order={order}
      session={session}
      camera={camera}
      cameraSrc="cam2"
      canCount
      canLoad
      isKiosk
      busy={false}
      bagCounterRef={createRef<BagCounterHandle>()}
      onSaveBags={vi.fn().mockResolvedValue(undefined)}
      onAccept={vi.fn().mockResolvedValue({ ok: true, error: "" })}
      onResetAi={vi.fn()}
      onStopAi={vi.fn()}
      onSessionChanged={vi.fn()}
      finish={{ disabled: false, onClick: vi.fn() }}
    />,
  );
}

beforeEach(() => {
  mocks.aiStatus = {
    running: true,
    status: "online",
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
    renderDetail();

    const stream = screen.getByTestId("active-session-stream");
    expect(stream).toHaveAttribute("data-src", "cam2");
    expect(stream).not.toHaveAttribute("data-src", "cam2ai");
    expect(screen.getByText("Подключение видео")).toBeInTheDocument();
    expect(screen.queryByTestId("active-session-detections")).not.toBeInTheDocument();

    await user.click(stream);

    expect(screen.getByText("AI считает")).toBeInTheDocument();
    expect(screen.getByTestId("active-session-detections")).toHaveTextContent("Blue_50");
    expect(screen.getByTestId("active-session-detections")).toHaveAttribute("data-frame", "640x360");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-line", "0.1,0.4,0.9,0.6");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-direction", "negative");
  });

  it("использует сохранённую линию камеры, если процессор ещё не прислал живую", async () => {
    mocks.aiStatus = { running: true, status: "online", stream: "cam2ai", total: 0 };
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByTestId("active-session-stream"));

    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-line", "0.2,0.3,0.8,0.3");
    expect(screen.getByTestId("active-session-line")).toHaveAttribute("data-direction", "down");
  });
});
