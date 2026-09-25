import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WagonArchCameraRuntime } from "@/lib/types";
import { WagonArchCameraPanel } from "./wagon-arch-camera";

const mocks = vi.hoisted(() => ({
  runtime: null as WagonArchCameraRuntime | null,
  reload: vi.fn(),
  setData: vi.fn(),
  polling: vi.fn(),
  put: vi.fn(),
  showSuccess: vi.fn(),
  auth: { isSuperuser: true },
}));
vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="camera-stream" data-src={src} />,
}));
vi.mock("@/lib/use-video-box", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/use-video-box")>()),
  useVideoBox: () => ({ left: 0, top: 0, width: 640, height: 360 }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: mocks.runtime, loading: false, error: "", reload: mocks.reload, setData: mocks.setData }),
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.polling }));
vi.mock("@/lib/api", () => ({ api: { put: mocks.put }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/toast", () => ({ showSuccess: mocks.showSuccess }));
vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean; permissions: string[] } }) => unknown) =>
    selector({ me: { is_superuser: mocks.auth.isSuperuser, permissions: [] } }),
}));

const ZONE = {
  cam: "cam8",
  configured: true,
  enabled: true,
  source: "main",
  coordinate_space: "normalized",
  points: [
    { x: 0.1, y: 0.3 },
    { x: 0.9, y: 0.3 },
    { x: 0.9, y: 0.7 },
    { x: 0.1, y: 0.7 },
  ],
  updated_at: "2026-09-14T05:00:00+00:00",
};

function runtime(overrides: Partial<WagonArchCameraRuntime> = {}): WagonArchCameraRuntime {
  return {
    camera: "cam8",
    source: "main",
    stream: "cam8",
    automation_enabled: true,
    zone: ZONE,
    motion: { state: "still", still_seconds: 12.5, direction: "right", status: "online", sample_age_seconds: 0.3 },
    runtime: {
      enabled: true,
      camera: "cam8",
      collector: { total: 12, pending: 0, status: "running", standing: "stop-1", motion: "still", heartbeat_at: 1 },
      pending_stops: 1,
      attention_stops: 0,
      last_stop: {
        id: 7,
        stop_id: "stop-1",
        number: "28055531",
        status: "open",
        full_weight_kg: 62340,
        exit_weight_kg: null,
        arrived_at: "2026-09-14T04:00:00Z",
        wagon_id: 124,
        blocked_reason: "silo_required",
        blocked_detail: "",
      },
      updated_at: "2026-09-14T05:00:00Z",
    },
    diagnostic: "",
    ...overrides,
  };
}

beforeEach(() => {
  mocks.runtime = runtime();
  mocks.auth.isSuperuser = true;
  mocks.put.mockReset();
});

describe("WagonArchCameraPanel", () => {
  it("shows the stream, the zone, the motion state and the collector status", () => {
    render(<WagonArchCameraPanel />);
    expect(screen.getByTestId("camera-stream")).toHaveAttribute("data-src", "cam8");
    expect(screen.getByTestId("vehicle-roi-polygon")).toBeInTheDocument();
    expect(screen.getByText("Вагон стоит · 13 с")).toBeInTheDocument();
    expect(screen.getByText(/Сборщик: работает/)).toBeInTheDocument();
    expect(screen.getByText(/Вагон 28055531/)).toBeInTheDocument();
    expect(screen.getByText("Назначьте силос в рейсе — заезд запишется автоматически")).toBeInTheDocument();
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
  });

  it("shows «—» in the status rows before the first runtime response, not «выключена»", () => {
    mocks.runtime = null;
    render(<WagonArchCameraPanel />);
    const rows = screen.getAllByText("—");
    expect(rows.length).toBeGreaterThanOrEqual(2); // зона + автоматика, at least
    expect(screen.queryByText("выключена")).toBeNull();
    expect(screen.queryByText("Зона арки не задана")).toBeNull();
  });

  it("explains a missing zone and an unavailable camera pc", () => {
    mocks.runtime = runtime({
      zone: { ...ZONE, configured: false, enabled: false, points: [] },
      motion: null,
      diagnostic: "Движение недоступно: not configured",
    });
    render(<WagonArchCameraPanel />);
    expect(screen.getByText("Зона арки не задана")).toBeInTheDocument();
    expect(screen.getByText("Нет данных о движении")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Движение недоступно");
  });

  it("lets a superuser edit and save the zone and keeps a 503 partial save", async () => {
    const user = userEvent.setup();
    mocks.put.mockResolvedValueOnce({ data: { saved: true, applied_to_monitor: true, zone: ZONE } });
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(mocks.put).toHaveBeenCalledWith(
      "/cameras/cam8/wagon-arch-runtime/",
      { points: ZONE.points, enabled: true, source: "main" },
      { timeout: 12_000 },
    );
    expect(mocks.setData).toHaveBeenCalledWith(expect.objectContaining({ zone: ZONE }));
    expect(mocks.showSuccess).toHaveBeenCalledWith("Зона арки сохранена");

    mocks.put.mockRejectedValueOnce({
      response: {
        status: 503,
        data: { saved: true, applied_to_monitor: false, zone: ZONE, code: "zone_saved_refresh_pending" },
      },
      message: "503",
    });
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(screen.getByRole("status")).toHaveTextContent("монитор пока не подтвердил");
  });

  it("hides the editor from non-superusers", async () => {
    mocks.auth.isSuperuser = false;
    render(<WagonArchCameraPanel />);
    expect(screen.queryByRole("button", { name: "Изменить зону" })).toBeNull();
  });

  it("keeps the editor open and polling paused when a save is rejected outright", async () => {
    const user = userEvent.setup();
    mocks.put.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Зона выходит за пределы кадра" } },
      message: "Зона выходит за пределы кадра",
    });
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Зона выходит за пределы кадра");
    // Editor still open: the cancel/save controls are still there, not «Изменить зону».
    expect(screen.getByRole("button", { name: "Отмена" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить зону" })).toBeInTheDocument();
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
  });

  it("does not accept a saved zone without area, like the vehicle camera", async () => {
    const user = userEvent.setup();
    const flatZone = {
      ...ZONE,
      points: [
        { x: 0.1, y: 0.1 },
        { x: 0.5, y: 0.5 },
        { x: 0.9, y: 0.9 },
      ],
    };
    mocks.put.mockResolvedValueOnce({ data: { saved: true, applied_to_monitor: true, zone: flatZone } });
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Некорректный ответ сохранения зоны");
    expect(mocks.setData).not.toHaveBeenCalledWith(expect.objectContaining({ zone: flatZone }));
    expect(screen.getByRole("button", { name: "Сохранить зону" })).toBeInTheDocument();
  });

  it("resumes polling and makes no PUT when the editor is cancelled", async () => {
    const user = userEvent.setup();
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByRole("button", { name: "Изменить зону" })).toBeInTheDocument();
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
    expect(mocks.put).not.toHaveBeenCalled();
  });

  it("resumes polling after a successful save", async () => {
    const user = userEvent.setup();
    mocks.put.mockResolvedValueOnce({ data: { saved: true, applied_to_monitor: true, zone: ZONE } });
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
  });
});

describe("WagonArchCameraPanel — камера проходной", () => {
  it("показывает закреплённую камеру, кнопку назначения и примечание", () => {
    render(
      <WagonArchCameraPanel assignedCamera="cam8" assignAction={<button type="button">Назначить камеру</button>} />,
    );
    // Назначение — настройка CRM, без статуса синхронизации с ПК камер.
    expect(screen.getByText("cam8")).toBeInTheDocument();
    expect(screen.queryByText(/ожидает связь/)).toBeNull();
    expect(screen.getByRole("button", { name: "Назначить камеру" })).toBeInTheDocument();
    expect(screen.getByText(/Эта камера отвечает только за номера вагонов/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("предупреждает, когда камера номеров и камера арки разные", () => {
    render(<WagonArchCameraPanel assignedCamera="cam3" />);
    expect(screen.getByText("cam3")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Камера номеров (cam3) и камера арки (cam8) должны совпадать.");
  });

  it("без назначенной камеры пишет «не назначена» и стримит камеру арки", () => {
    render(<WagonArchCameraPanel />);
    expect(screen.getByText("не назначена")).toBeInTheDocument();
    expect(screen.getByTestId("camera-stream")).toHaveAttribute("data-src", "cam8");
  });

  it("пока прокси не ответил, стримит закреплённую камеру", () => {
    mocks.runtime = null;
    render(<WagonArchCameraPanel assignedCamera="cam8" />);
    expect(screen.getByTestId("camera-stream")).toHaveAttribute("data-src", "cam8");
  });
});
