import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VehiclePlateCameraWorkspace, type VehiclePlateRuntime } from "./vehicle-plate-camera";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  reload: vi.fn(),
  visiblePolling: vi.fn(),
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="protected-camera-stream" data-src={src} />,
}));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.visiblePolling }));

function runtime(overrides: Partial<VehiclePlateRuntime> = {}): VehiclePlateRuntime {
  return {
    camera: "cam1",
    enabled: true,
    ready: true,
    automation_enabled: true,
    camera_configured: true,
    source: "main",
    server_push_configured: true,
    diagnostic: "online",
    monitor: {
      status: "online",
      source: "main",
      last_frame_at: "2026-08-28T10:00:00Z",
      last_inference_at: "2026-08-28T10:00:00Z",
      last_confirmed_at: null,
      scanned_frames: 240,
      plate_detections: 12,
      stationary_admissions: 2,
      ocr_attempts: 6,
      confirmed_events: 0,
      durable_duplicates: 0,
      consecutive_errors: 0,
      inference_avg_ms: 18.5,
      ocr_avg_ms: 22.4,
      has_error: false,
      stop_gate: { dwell_seconds: 3, min_frames: 6, max_movement_ratio: 0.018, exit_grace_seconds: 5 },
    },
    roi: {
      configured: true,
      enabled: true,
      source: "main",
      coordinate_space: "normalized",
      points: [
        { x: 0.2, y: 0.3 },
        { x: 0.8, y: 0.3 },
        { x: 0.9, y: 0.9 },
        { x: 0.1, y: 0.9 },
      ],
      updated_at: "2026-08-28T09:00:00Z",
    },
    ...overrides,
  };
}

function mockApi(data: VehiclePlateRuntime | null, error = "", loading = false) {
  mocks.useApi.mockReturnValue({ data, error, loading, reload: mocks.reload });
}

describe("VehiclePlateCameraWorkspace", () => {
  beforeEach(() => {
    mocks.useApi.mockReset();
    mocks.reload.mockReset();
    mocks.reload.mockResolvedValue(undefined);
    mocks.visiblePolling.mockReset();
    mockApi(runtime());
  });

  it("shows cam1 and polls its AI runtime without conflating it with the video signal", () => {
    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Камера проходной на вывоз" })).toBeInTheDocument();
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam1");
    expect(screen.getByText("Камера cam1 · OCR: main")).toBeInTheDocument();
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/cam1/vehicle-plate-runtime/");
    expect(mocks.visiblePolling).toHaveBeenCalledWith(mocks.reload, 5_000, true);

    // Video playback and model health are independent signals. This player
    // is offline while the backend reports a healthy inference monitor.
    expect(screen.getByText("ВИДЕО: НЕТ СИГНАЛА")).toBeInTheDocument();
    expect(screen.getAllByText("AI РАБОТАЕТ")).toHaveLength(2);
    expect(screen.getByText("Кадров: 240 · найдено номеров: 12")).toBeInTheDocument();
    expect(screen.getByText("ROI ПОКАЗАН")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Этапы распознавания номера" })).toHaveTextContent(
      "Кадры240Номера12Стоп2OCR6Готово0",
    );
    expect(screen.getByText("OCR запускался, но ещё нет трёх совпадающих чтений номера.")).toBeInTheDocument();
  });

  it("explains when the vehicle model itself is disabled", () => {
    mockApi(runtime({ enabled: false, ready: false, monitor: null, diagnostic: "model_disabled" }));

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("AI: ОТКЛЮЧЕНА")).toHaveLength(2);
    expect(screen.getByText("Модель номеров выключена на ПК камер.")).toBeInTheDocument();
    expect(screen.getByText("ВИДЕО: НЕТ СИГНАЛА")).toBeInTheDocument();
  });

  it("shows source mismatch and does not claim that the ROI is visible", () => {
    mockApi(
      runtime({
        roi: {
          configured: true,
          enabled: true,
          source: "sub",
          coordinate_space: "normalized",
          points: [
            { x: 0.1, y: 0.1 },
            { x: 0.9, y: 0.1 },
            { x: 0.9, y: 0.9 },
          ],
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText("ROI ДЛЯ ДРУГОГО ПОТОКА")).toBeInTheDocument();
    expect(screen.queryByText("ROI ПОКАЗАН")).not.toBeInTheDocument();
    expect(screen.getAllByText("ROI НЕ ГОТОВ")).toHaveLength(2);
  });

  it("fails closed when the runtime endpoint cannot be reached", () => {
    mockApi(runtime(), "ПК камер недоступен");

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("AI: НЕТ СВЯЗИ")).toHaveLength(2);
    expect(screen.getByText("ROI НЕДОСТУПЕН")).toBeInTheDocument();
    expect(screen.queryByText("ROI ПОКАЗАН")).not.toBeInTheDocument();
  });

  it("warns when recognition cannot deliver an event to the CRM", () => {
    mockApi(runtime({ server_push_configured: false }));

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("ОТПРАВКА НЕ НАСТРОЕНА")).toHaveLength(2);
    expect(screen.getByText("AI может прочитать номер, но не может передать событие в CRM.")).toBeInTheDocument();
  });

  it.each([
    ["reconnecting", "КАМЕРА: ПЕРЕПОДКЛЮЧЕНИЕ", "AI потерял поток камеры и пытается подключиться заново."],
    ["stopped", "AI: МОНИТОР ОСТАНОВЛЕН", "Обработка этой камеры остановлена."],
    ["warming", "AI ПРОГРЕВАЕТСЯ", "Поток подключён, ожидаем первый обработанный кадр."],
  ])("shows the %s capture state explicitly", (status, label, detail) => {
    const current = runtime();
    mockApi(
      runtime({
        server_push_configured: false,
        monitor: { ...current.monitor!, status, consecutive_errors: 2, has_error: true },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText(label)).toHaveLength(2);
    expect(screen.getByText(detail)).toBeInTheDocument();
  });

  it("does not let a delivery warning mask a transient startup state", () => {
    const current = runtime();
    mockApi(
      runtime({
        server_push_configured: false,
        monitor: { ...current.monitor!, status: "starting" },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("AI ЗАПУСКАЕТСЯ")).toHaveLength(2);
    expect(screen.queryByText("ОТПРАВКА НЕ НАСТРОЕНА")).not.toBeInTheDocument();
  });

  it("rejects a monitor whose source differs from automation", () => {
    const current = runtime();
    mockApi(runtime({ monitor: { ...current.monitor!, source: "sub" } }));

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("ПОТОК МОНИТОРА НЕ СОВПАЛ")).toHaveLength(2);
  });

  it("does not start polling while the initial runtime request is pending", () => {
    mockApi(null, "", true);

    render(<VehiclePlateCameraWorkspace />);

    expect(mocks.visiblePolling).toHaveBeenCalledWith(mocks.reload, 5_000, false);
  });
});
