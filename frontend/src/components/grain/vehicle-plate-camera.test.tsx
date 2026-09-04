import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  VehiclePlateCameraWorkspace,
  type ScaleAutomationRuntime,
  type VehiclePlateRuntime,
} from "./vehicle-plate-camera";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  reload: vi.fn(),
  scaleReload: vi.fn(),
  settingsReload: vi.fn(),
  setData: vi.fn(),
  scaleSetData: vi.fn(),
  settingsSetData: vi.fn(),
  visiblePolling: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  apiError: vi.fn(),
  showSuccess: vi.fn(),
  auth: { isSuperuser: false, permissions: [] as string[] },
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="protected-camera-stream" data-src={src} />,
}));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.visiblePolling }));
vi.mock("@/lib/api", () => ({
  api: { patch: mocks.patch, post: mocks.post, put: mocks.put },
  apiError: mocks.apiError,
}));
vi.mock("@/lib/toast", () => ({ showSuccess: mocks.showSuccess }));
vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean; permissions: string[] } }) => unknown) =>
    selector({ me: { is_superuser: mocks.auth.isSuperuser, permissions: mocks.auth.permissions } }),
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
    scale_automation: {
      enabled: false,
      stable_weight_seconds: 10,
      state: "disabled",
      last_checked_at: "2026-09-03T07:30:00Z",
      heartbeat_stale: false,
      active: null,
    },
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

function mockApi(
  data: VehiclePlateRuntime | null,
  error = "",
  loading = false,
  scaleData: ScaleAutomationRuntime | null = data?.scale_automation ?? null,
  scaleError = "",
  scaleLoading = false,
  settingsData: { stable_weight_seconds: number } | null = { stable_weight_seconds: 10 },
  settingsError = "",
  settingsLoading = false,
) {
  mocks.useApi.mockImplementation((url: string) => {
    if (url === "/grain/automatic-passage-scale/runtime/") {
      return {
        data: scaleData,
        error: scaleError,
        loading: scaleLoading,
        reload: mocks.scaleReload,
        setData: mocks.scaleSetData,
      };
    }
    if (url === "/grain/automatic-passage-scale/settings/") {
      return {
        data: settingsData,
        error: settingsError,
        loading: settingsLoading,
        reload: mocks.settingsReload,
        setData: mocks.settingsSetData,
      };
    }
    return { data, error, loading, reload: mocks.reload, setData: mocks.setData };
  });
}

describe("VehiclePlateCameraWorkspace", () => {
  beforeEach(() => {
    mocks.useApi.mockReset();
    mocks.reload.mockReset();
    mocks.reload.mockResolvedValue(undefined);
    mocks.scaleReload.mockReset();
    mocks.scaleReload.mockResolvedValue(undefined);
    mocks.settingsReload.mockReset();
    mocks.settingsReload.mockResolvedValue(undefined);
    mocks.setData.mockReset();
    mocks.scaleSetData.mockReset();
    mocks.settingsSetData.mockReset();
    mocks.visiblePolling.mockReset();
    mocks.patch.mockReset();
    mocks.put.mockReset();
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({ data: {} });
    mocks.apiError.mockReset();
    mocks.apiError.mockReturnValue("Не удалось сохранить ROI");
    mocks.showSuccess.mockReset();
    mocks.auth.isSuperuser = false;
    mocks.auth.permissions = [];
    mockApi(runtime());
  });

  it("uses the runtime bootstrap and keeps video health separate from AI health", () => {
    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Камера проходной на вывоз" })).toBeInTheDocument();
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam1main");
    expect(screen.getByText("Камера cam1 · поток/OCR: main")).toBeInTheDocument();
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/vehicle-plate-runtime/");
    expect(mocks.useApi).toHaveBeenCalledWith("/grain/automatic-passage-scale/runtime/");
    expect(mocks.useApi).toHaveBeenCalledWith("/grain/automatic-passage-scale/settings/");
    expect(mocks.visiblePolling).toHaveBeenCalledWith(mocks.reload, 5_000, true);
    expect(mocks.visiblePolling).toHaveBeenCalledWith(mocks.settingsReload, 5_000, true);

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
    const current = runtime();
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
        scale_automation: {
          ...current.scale_automation!,
          enabled: true,
          state: "idle",
        },
        roi: {
          ...runtime().roi,
          source: "sub",
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText("Автоматический вывоз по весам")).toBeInTheDocument();
    expect(screen.getByText("Вес → номер → статус")).toBeInTheDocument();
    expect(screen.getAllByText("AI ГОТОВА")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent(
      "ОЖИДАЕТ МАШИНУФоновый сервис проверяет весы каждую секунду",
    );
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam7");
    expect(screen.getByText("Камера cam7 · поток/OCR: sub")).toBeInTheDocument();
    expect(screen.queryByText("АВТОМАТИКА ВЫКЛ.")).not.toBeInTheDocument();
    expect(screen.queryByText("ОТПРАВКА НЕ НАСТРОЕНА")).not.toBeInTheDocument();
  });

  it.each([
    ["candidate", "ЖДЁТ СТАБИЛЬНЫЙ ВЕС", "Обнаружено изменение веса"],
    ["recognizing", "РАСПОЗНАЁТ НОМЕР", "Камера обрабатывает новый стабильный заезд"],
    ["applying", "ОБНОВЛЯЕТ РЕЙС", "следующий статус рейса сохраняются"],
    ["awaiting_clear", "ЖДЁТ ОСВОБОЖДЕНИЯ ВЕСОВ", "машина съедет с весов"],
    ["unavailable", "ВЕСЫ НЕДОСТУПНЫ", "Используйте ручное оформление"],
  ] as const)("shows the independent %s scale-automation state", (state, label, detail) => {
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        monitor: null,
        scale_automation: { ...current.scale_automation!, enabled: true, state },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("AI ГОТОВА")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent(label);
    expect(screen.getByText(new RegExp(detail))).toBeInTheDocument();
  });

  it("directs an operator to the affected trip when automation needs manual help", () => {
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        monitor: null,
        scale_automation: {
          ...current.scale_automation!,
          enabled: true,
          state: "manual_required",
          active: {
            request_id: "c4e7a4b1-7d77-4700-9ca7-f37b82083815",
            stage: "done",
            action: "exit",
            wagon_id: 91,
            retryable: false,
            error_code: "plate_mismatch",
          },
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getAllByText("AI ГОТОВА")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent(
      "НУЖЕН ОПЕРАТОРАвтоматика остановила рейс #91; завершите его ручными кнопками.",
    );
    expect(screen.queryByRole("button", { name: "Подтвердить ручную обработку" })).not.toBeInTheDocument();
    expect(screen.queryByText("c4e7a4b1-7d77-4700-9ca7-f37b82083815")).not.toBeInTheDocument();
  });

  it("keeps a latched manual passage actionable when Camera-PC runtime is unavailable", async () => {
    mocks.auth.permissions = ["grain.weigh"];
    const current = runtime();
    const requestId = "c4e7a4b1-7d77-4700-9ca7-f37b82083815";
    const scaleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "manual_required",
      heartbeat_stale: true,
      active: {
        request_id: requestId,
        stage: "done",
        action: "exit",
        wagon_id: 91,
        retryable: false,
        error_code: "plate_mismatch",
      },
    };
    const acknowledgedAutomation: ScaleAutomationRuntime = {
      ...scaleAutomation,
      state: "awaiting_clear",
    };
    mocks.post.mockResolvedValue({
      data: { acknowledged: true, scale_automation: acknowledgedAutomation },
    });
    mocks.scaleReload.mockRejectedValueOnce(new Error("runtime reload offline"));
    mockApi(null, "AI-сервис камер недоступен", false, scaleAutomation);

    render(<VehiclePlateCameraWorkspace />);
    expect(screen.getAllByText("AI: НЕТ СВЯЗИ")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("НУЖЕН ОПЕРАТОР");
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить ручную обработку" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/grain/automatic-passage-scale/acknowledge/", {
        request_id: requestId,
        resolved: true,
      }),
    );
    await waitFor(() => expect(mocks.scaleReload).toHaveBeenCalled());
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(mocks.setData).not.toHaveBeenCalled();
    expect(mocks.scaleSetData).toHaveBeenCalledWith(acknowledgedAutomation);
    expect(mocks.showSuccess).toHaveBeenCalledWith("Ручная обработка подтверждена");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a latched manual passage actionable after automation is disabled", () => {
    mocks.auth.permissions = ["grain.weigh"];
    const current = runtime();
    const requestId = "c4e7a4b1-7d77-4700-9ca7-f37b82083815";
    mockApi(
      runtime({ weight_first_enabled: false, monitor: null, scale_automation: undefined }),
      "AI-сервис камер недоступен",
      false,
      {
        ...current.scale_automation!,
        enabled: false,
        state: "manual_required",
        active: {
          request_id: requestId,
          stage: "done",
          action: null,
          wagon_id: null,
          retryable: false,
          error_code: "automatic_scale_disabled",
        },
      },
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("НУЖЕН ОПЕРАТОР");
    expect(screen.getByRole("button", { name: "Подтвердить ручную обработку" })).toBeEnabled();
    expect(screen.queryByText(requestId)).not.toBeInTheDocument();
  });

  it("shows acknowledgement loading and request errors without exposing the request id", async () => {
    mocks.auth.permissions = ["grain.weigh"];
    const current = runtime();
    const requestId = "c4e7a4b1-7d77-4700-9ca7-f37b82083815";
    let rejectRequest: (reason?: unknown) => void = () => undefined;
    mocks.post.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectRequest = reject;
      }),
    );
    mockApi(
      runtime({
        scale_automation: {
          ...current.scale_automation!,
          enabled: true,
          state: "manual_required",
          active: {
            request_id: requestId,
            stage: "done",
            action: null,
            wagon_id: null,
            retryable: false,
            error_code: "recognition_failed",
          },
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить ручную обработку" }));

    expect(screen.getByRole("button", { name: "Подтверждение…" })).toBeDisabled();
    expect(screen.queryByText(requestId)).not.toBeInTheDocument();
    await act(async () => rejectRequest(new Error("offline")));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось подтвердить ручную обработку");
    expect(screen.getByRole("button", { name: "Подтвердить ручную обработку" })).toBeEnabled();
    expect(mocks.scaleReload).not.toHaveBeenCalled();
  });

  it("does not offer acknowledgement without an active manual-required request", () => {
    mocks.auth.permissions = ["grain.weigh"];
    const current = runtime();
    mockApi(
      runtime({
        scale_automation: {
          ...current.scale_automation!,
          enabled: true,
          state: "manual_required",
          active: null,
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.queryByRole("button", { name: "Подтвердить ручную обработку" })).not.toBeInTheDocument();
  });

  it("fails closed when the automatic watcher heartbeat is stale", () => {
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        scale_automation: {
          ...current.scale_automation!,
          enabled: true,
          state: "idle",
          heartbeat_stale: true,
        },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("АВТОМАТИКА НЕ ОТВЕЧАЕТ");
    expect(screen.queryByText("ОЖИДАЕТ МАШИНУ")).not.toBeInTheDocument();
  });

  it("fails closed when a standalone poll errors after retaining idle data", () => {
    const current = runtime();
    const idleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "idle",
    };
    mockApi(runtime({ scale_automation: idleAutomation }), "", false, idleAutomation, "CRM runtime недоступен");

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("АВТОМАТИКА: НЕТ СВЯЗИ");
    expect(screen.queryByText("ОЖИДАЕТ МАШИНУ")).not.toBeInTheDocument();
  });

  it("uses the embedded runtime as a rolling-deploy fallback while Camera-PC is healthy", () => {
    const current = runtime();
    const idleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "idle",
    };
    mockApi(runtime({ scale_automation: idleAutomation }), "", false, null, "Endpoint not deployed");

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("ОЖИДАЕТ МАШИНУ");
    expect(screen.queryByText("АВТОМАТИКА: НЕТ СВЯЗИ")).not.toBeInTheDocument();
  });

  it("does not report automation as healthy when an older runtime omits its state", () => {
    mockApi(runtime({ weight_first_enabled: true, scale_automation: undefined }));

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent("СТАТУС АВТОМАТИКИ НЕДОСТУПЕН");
    expect(screen.queryByText("ОЖИДАЕТ МАШИНУ")).not.toBeInTheDocument();
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

  it("keeps stable-weight settings read-only for an ordinary grain user", () => {
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        scale_automation: { ...current.scale_automation!, enabled: true, state: "idle" },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText(/Вес должен оставаться стабильным 10 секунд/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Настроить ожидание" })).not.toBeInTheDocument();
  });

  it("lets a superuser change the stable-weight wait from its current value", async () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    const scaleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "idle",
    };
    mockApi(runtime({ weight_first_enabled: true, scale_automation: scaleAutomation }), "", false, scaleAutomation);
    mocks.patch.mockResolvedValue({ data: { stable_weight_seconds: 15 } });

    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Настроить ожидание" }));
    const input = screen.getByRole("spinbutton", { name: "Время стабильного веса" });
    expect(input).toHaveValue(10);

    fireEvent.change(input, { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(mocks.patch).toHaveBeenCalledWith("/grain/automatic-passage-scale/settings/", {
        stable_weight_seconds: 15,
      }),
    );
    expect(mocks.settingsSetData).toHaveBeenCalledWith({ stable_weight_seconds: 15 });
    expect(mocks.scaleSetData).toHaveBeenCalledWith({ ...scaleAutomation, stable_weight_seconds: 15 });
    expect(mocks.scaleReload).toHaveBeenCalledTimes(1);
    expect(mocks.settingsReload).toHaveBeenCalledTimes(1);
    expect(mocks.showSuccess).toHaveBeenCalledWith("Время ожидания стабильного веса сохранено");
    expect(screen.queryByRole("dialog", { name: "Ожидание стабильного веса" })).not.toBeInTheDocument();
  });

  it.each(["", "1", "61", "10.5"])("does not submit an invalid stable-weight wait of %j", (value) => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        scale_automation: { ...current.scale_automation!, enabled: true, state: "idle" },
      }),
    );
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Настроить ожидание" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Время стабильного веса" }), {
      target: { value },
    });

    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("keeps the settings dialog open when saving fails", async () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    mockApi(
      runtime({
        weight_first_enabled: true,
        scale_automation: { ...current.scale_automation!, enabled: true, state: "idle" },
      }),
    );
    mocks.patch.mockRejectedValue(new Error("network"));
    mocks.apiError.mockReturnValue("Не удалось сохранить время ожидания");
    render(<VehiclePlateCameraWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "Настроить ожидание" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить время ожидания");
    expect(screen.getByRole("dialog", { name: "Ожидание стабильного веса" })).toBeInTheDocument();
    expect(mocks.settingsSetData).not.toHaveBeenCalled();
    expect(mocks.showSuccess).not.toHaveBeenCalled();
  });

  it("disables stable-weight mutation when the settings endpoint is unavailable", () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    const scaleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "idle",
    };
    mockApi(
      runtime({ weight_first_enabled: true, scale_automation: scaleAutomation }),
      "",
      false,
      scaleAutomation,
      "",
      false,
      null,
      "Настройки недоступны",
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("button", { name: "Настроить ожидание" })).toBeDisabled();
    expect(screen.getByText(/Вес должен оставаться стабильным 10 секунд/)).toBeInTheDocument();
  });

  it("uses the dedicated settings response when rolling runtime data omits the duration", () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    const legacyScaleAutomation = { ...current.scale_automation! };
    delete legacyScaleAutomation.stable_weight_seconds;
    mockApi(
      runtime({
        weight_first_enabled: true,
        scale_automation: { ...legacyScaleAutomation, enabled: true, state: "idle" },
      }),
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText(/Вес должен оставаться стабильным 10 секунд/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Автоматика весов" })).toHaveTextContent(
      "вес останется стабильным 10 секунд",
    );
    expect(screen.getByRole("button", { name: "Настроить ожидание" })).toBeEnabled();
  });

  it("prefills from the fresher polled runtime instead of a stale settings snapshot", () => {
    mocks.auth.isSuperuser = true;
    const current = runtime();
    const scaleAutomation: ScaleAutomationRuntime = {
      ...current.scale_automation!,
      enabled: true,
      state: "idle",
      stable_weight_seconds: 20,
    };
    mockApi(
      runtime({ weight_first_enabled: true, scale_automation: scaleAutomation }),
      "",
      false,
      scaleAutomation,
      "",
      false,
      { stable_weight_seconds: 10 },
    );

    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByText(/Вес должен оставаться стабильным 20 секунд/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Настроить ожидание" }));
    expect(screen.getByRole("spinbutton", { name: "Время стабильного веса" })).toHaveValue(20);
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
    expect(mocks.visiblePolling).toHaveBeenCalledWith(mocks.reload, 5_000, false);

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
