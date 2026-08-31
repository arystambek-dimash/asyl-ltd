import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VehiclePlateCameraWorkspace, type VehiclePlateRuntime } from "./vehicle-plate-camera";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  reload: vi.fn(),
  setData: vi.fn(),
  visiblePolling: vi.fn(),
  put: vi.fn(),
  apiError: vi.fn(),
  showSuccess: vi.fn(),
  auth: { isSuperuser: false },
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="protected-camera-stream" data-src={src} />,
}));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.visiblePolling }));
vi.mock("@/lib/api", () => ({ api: { put: mocks.put }, apiError: mocks.apiError }));
vi.mock("@/lib/toast", () => ({ showSuccess: mocks.showSuccess }));
vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean } }) => unknown) =>
    selector({ me: { is_superuser: mocks.auth.isSuperuser } }),
}));

function runtime(overrides: Partial<VehiclePlateRuntime> = {}): VehiclePlateRuntime {
  return {
    camera: "cam1",
    enabled: true,
    ready: true,
    automation_enabled: true,
    camera_configured: true,
    weight_first_enabled: false,
    on_demand_enabled: true,
    on_demand_camera_configured: true,
    source: "main",
    stream: "cam1main",
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
  mocks.useApi.mockReturnValue({ data, error, loading, reload: mocks.reload, setData: mocks.setData });
}

describe("VehiclePlateCameraWorkspace", () => {
  beforeEach(() => {
    mocks.useApi.mockReset();
    mocks.reload.mockReset();
    mocks.reload.mockResolvedValue(undefined);
    mocks.setData.mockReset();
    mocks.visiblePolling.mockReset();
    mocks.put.mockReset();
    mocks.apiError.mockReset();
    mocks.apiError.mockReturnValue("Не удалось сохранить ROI");
    mocks.showSuccess.mockReset();
    mocks.auth.isSuperuser = false;
    mockApi(runtime());
  });

  it("uses the runtime bootstrap and keeps video health separate from AI health", () => {
    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Камера проходной на вывоз" })).toBeInTheDocument();
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam1main");
    expect(screen.getByText("Камера cam1 · поток/OCR: main")).toBeInTheDocument();
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/vehicle-plate-runtime/");
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

  it("shows the weight-first camera as ready without a legacy monitor or webhook", () => {
    mockApi(
      runtime({
        camera: "cam7",
        source: "sub",
        stream: "cam7",
        weight_first_enabled: true,
        automation_enabled: false,
        camera_configured: false,
        server_push_configured: false,
        diagnostic: "on_demand_ready",
        monitor: null,
        roi: {
          ...runtime().roi,
          source: "sub",
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText("Камера распознавания после веса")).toBeInTheDocument();
    expect(screen.getByText("Вес → номер → рейс")).toBeInTheDocument();
    expect(screen.getAllByText("AI ГОТОВА")).toHaveLength(2);
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam7");
    expect(screen.getByText("Камера cam7 · поток/OCR: sub")).toBeInTheDocument();
    expect(screen.queryByText("АВТОМАТИКА ВЫКЛ.")).not.toBeInTheDocument();
    expect(screen.queryByText("ОТПРАВКА НЕ НАСТРОЕНА")).not.toBeInTheDocument();
  });

  it("shows source mismatch and does not claim that the ROI is visible", () => {
    mockApi(
      runtime({
        camera: "cam7",
        source: "sub",
        stream: "cam7",
        weight_first_enabled: true,
        roi: {
          configured: true,
          enabled: true,
          source: "main",
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

  it("keeps ROI read-only for an ordinary grain user", () => {
    render(<VehiclePlateCameraWorkspace />);

    expect(screen.queryByRole("button", { name: "Изменить ROI" })).not.toBeInTheDocument();
    expect(screen.getByTestId("vehicle-roi-layer")).toHaveAttribute("aria-hidden", "true");
  });

  it("lets a superuser enter and cancel editing without changing the server ROI", () => {
    mocks.auth.isSuperuser = true;
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить ROI" }));

    expect(screen.getByRole("status")).toHaveTextContent("Перетащите голубые точки");
    expect(screen.getByTestId("vehicle-roi-layer")).toHaveAttribute("aria-label", "Редактор зоны остановки");
    expect(mocks.visiblePolling).toHaveBeenLastCalledWith(mocks.reload, 5_000, false);

    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

    expect(screen.getByRole("button", { name: "Изменить ROI" })).toBeInTheDocument();
    expect(mocks.put).not.toHaveBeenCalled();
    expect(screen.getByTestId("vehicle-roi-layer")).toHaveAttribute("aria-hidden", "true");
  });

  it("saves ROI to the configured camera and source from runtime", async () => {
    mocks.auth.isSuperuser = true;
    const current = runtime({
      camera: "cam7",
      source: "sub",
      stream: "cam7",
      weight_first_enabled: true,
      roi: { ...runtime().roi, source: "sub" },
    });
    const savedRoi = {
      ...current.roi,
      points: [
        { x: 0.2, y: 0.3 },
        { x: 0.8, y: 0.3 },
        { x: 0.9, y: 0.9 },
        { x: 0.1, y: 0.9 },
      ],
      updated_at: "2026-08-28T11:00:00Z",
    };
    mockApi(current);
    mocks.put.mockResolvedValue({ data: { saved: true, applied_to_monitor: true, roi: savedRoi } });
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить ROI" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ROI" }));

    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith(
        "/cameras/cam7/vehicle-plate-runtime/",
        {
          points: [
            { x: 0.2, y: 0.3 },
            { x: 0.8, y: 0.3 },
            { x: 0.9, y: 0.9 },
            { x: 0.1, y: 0.9 },
          ],
          enabled: true,
          source: "sub",
        },
        { timeout: 12_000 },
      ),
    );
    expect(mocks.setData).toHaveBeenCalledWith({ ...current, roi: savedRoi });
    expect(screen.getByText(/Следующее распознавание после стабильного веса/)).toBeInTheDocument();
    expect(mocks.showSuccess).toHaveBeenCalledWith("ROI камеры сохранён");
  });

  it("keeps a ROI persisted by a 503 response and warns about delayed live refresh", async () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    const savedRoi = { ...current.roi, updated_at: "2026-08-28T11:00:00Z" };
    mockApi(current);
    mocks.put.mockRejectedValue({
      response: { status: 503, data: { saved: true, applied_to_monitor: false, roi: savedRoi } },
    });
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить ROI" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ROI" }));

    expect(await screen.findByText(/ROI сохранён, но монитор пока не подтвердил обновление/)).toBeInTheDocument();
    expect(mocks.setData).toHaveBeenCalledWith({ ...current, roi: savedRoi });
    expect(mocks.apiError).not.toHaveBeenCalled();
    expect(mocks.showSuccess).not.toHaveBeenCalled();
  });

  it("does not trust a saved-looking payload from a non-503 error", async () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    mockApi(current);
    mocks.put.mockRejectedValue({
      response: {
        status: 400,
        data: { saved: true, applied_to_monitor: false, roi: current.roi },
      },
    });
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить ROI" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ROI" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить ROI");
    expect(mocks.setData).not.toHaveBeenCalled();
    expect(mocks.apiError).toHaveBeenCalled();
  });

  it("keeps the editor open when saving fails before persistence", async () => {
    mocks.auth.isSuperuser = true;
    mocks.put.mockRejectedValue(new Error("offline"));
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Изменить ROI" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ROI" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить ROI");
    expect(screen.getByRole("button", { name: "Отмена" })).toBeInTheDocument();
    expect(mocks.setData).not.toHaveBeenCalled();
  });
});
