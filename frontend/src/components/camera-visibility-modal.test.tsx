import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VehicleRoiConfig } from "@/lib/types";
import { installVideoGeometry, restoreVideoGeometry } from "@/test-utils/video-geometry";
import { CameraTile } from "./camera-wall";
import { CameraVisibilityMask } from "./camera-visibility-mask";
import { CameraVisibilityModal } from "./camera-visibility-modal";

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, put: mocks.put },
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: () => <video data-testid="live-video" style={{ objectFit: "contain" }} />,
}));

const URL = "/cameras/cam3/visibility-zone/";
const SQUARE = [
  { x: 0.2, y: 0.1 },
  { x: 0.8, y: 0.1 },
  { x: 0.8, y: 0.9 },
  { x: 0.2, y: 0.9 },
];

function zone(overrides: Partial<VehicleRoiConfig> = {}): VehicleRoiConfig {
  return {
    configured: true,
    enabled: true,
    source: "main",
    coordinate_space: "normalized",
    points: SQUARE,
    updated_at: "2026-10-15T07:00:00+00:00",
    ...overrides,
  };
}

const EMPTY = zone({ configured: false, enabled: false, points: [], updated_at: null });

function serve(saved: VehicleRoiConfig) {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === URL) return { data: saved };
    return new Promise(() => undefined);
  });
}

function openModal(onZoneChange = vi.fn()) {
  render(
    <CameraVisibilityModal
      camera={{ src: "cam3", zone: "Конвейер вагон" }}
      onClose={vi.fn()}
      onZoneChange={onZoneChange}
    />,
  );
  return { dialog: screen.getByRole("dialog"), onZoneChange };
}

beforeEach(() => {
  mocks.get.mockReset();
  mocks.put.mockReset();
  installVideoGeometry();
});

afterEach(restoreVideoGeometry);

describe("CameraVisibilityModal", () => {
  it("draws a new zone and saves it to the camera PC", async () => {
    serve(EMPTY);
    const { dialog, onZoneChange } = openModal();

    await waitFor(() => expect(within(dialog).getByRole("button", { name: /Задать зону/ })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: /Задать зону/ }));
    fireEvent.keyDown(within(dialog).getByRole("button", { name: "Точка зоны 1" }), { key: "ArrowRight" });
    expect(within(dialog).getByText(/4 из 12 точек/)).toBeInTheDocument();

    mocks.put.mockResolvedValue({ data: { saved: true, applied_to_monitor: true, zone: zone() } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Сохранить зону/ }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put).toHaveBeenCalledWith(
      URL,
      {
        points: [
          { x: 0.005, y: 0 },
          { x: 1, y: 0 },
          { x: 1, y: 1 },
          { x: 0, y: 1 },
        ],
        enabled: true,
        source: "main",
      },
      { timeout: 12_000 },
    );
    expect(
      await within(dialog).findByText("Зона сохранена: AI и экраны видят только выделенную область."),
    ).toBeInTheDocument();
    expect(onZoneChange).toHaveBeenLastCalledWith("cam3", zone());
    expect(within(dialog).getByRole("button", { name: /Выключить маску/ })).toBeInTheDocument();
  });

  it("switches a saved mask off without losing its polygon", async () => {
    serve(zone());
    mocks.put.mockResolvedValue({
      data: { saved: true, applied_to_monitor: true, zone: zone({ enabled: false }) },
    });
    const { dialog, onZoneChange } = openModal();

    fireEvent.click(await within(dialog).findByRole("button", { name: /Выключить маску/ }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put.mock.calls[0][1]).toEqual({ points: SQUARE, enabled: false, source: "main" });
    expect(await within(dialog).findByText("Маска выключена: камера снова видит весь кадр.")).toBeInTheDocument();
    expect(onZoneChange).toHaveBeenLastCalledWith("cam3", zone({ enabled: false }));
    expect(within(dialog).getByRole("button", { name: /Включить маску/ })).toBeInTheDocument();
  });

  it("adds a point on a side while editing", async () => {
    serve(zone());
    const { dialog } = openModal();

    await waitFor(() => expect(within(dialog).getByRole("button", { name: /Изменить зону/ })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: /Изменить зону/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить точку между 1 и 2" }));

    expect(within(dialog).getByText(/5 из 12 точек/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Точка зоны 5" })).toBeInTheDocument();
  });

  it("does not allow saving when the camera PC cannot read the zone", async () => {
    mocks.get.mockRejectedValue({
      response: {
        status: 404,
        data: { detail: "ПК камер не поддерживает зону видимости для этой камеры — обновите AI-сервис" },
      },
    });
    const { dialog } = openModal();

    expect(await within(dialog).findByText(/обновите AI-сервис/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Задать зону/ })).toBeDisabled();
  });
});

describe("CameraVisibilityMask", () => {
  it("covers everything outside the zone over the contained video box", () => {
    render(
      <div>
        <video style={{ objectFit: "contain" }} />
        <CameraVisibilityMask zone={zone()} />
      </div>,
    );

    const mask = screen.getByTestId("camera-visibility-mask");
    expect(parseFloat(mask.style.top)).toBeCloseTo(75, 2);
    expect(parseFloat(mask.style.height)).toBeCloseTo(450, 2);
    expect(mask.querySelector("path")).toHaveAttribute(
      "d",
      "M0,0 H1000 V1000 H0 Z M200,100 L800,100 L800,900 L200,900 Z",
    );
    expect(mask.querySelector("path")).toHaveAttribute("fill-rule", "evenodd");
  });

  it.each([
    ["switched off", zone({ enabled: false })],
    ["not configured", EMPTY],
    ["missing", null],
  ])("leaves the frame open when the zone is %s", (_name, config) => {
    render(
      <div>
        <video style={{ objectFit: "contain" }} />
        <CameraVisibilityMask zone={config} />
      </div>,
    );

    expect(screen.getByTestId("camera-visibility-mask").querySelector("svg")).toBeNull();
  });
});

describe("CameraTile zone control", () => {
  const camera = {
    id: "nvr:cam3",
    name: "cam3",
    zone: "Конвейер вагон",
    src: "cam3",
    kind: "nvr-channel" as const,
    online: true,
  };

  it("opens the zone editor without opening the camera", () => {
    const onClick = vi.fn();
    const onConfigureZone = vi.fn();
    render(<CameraTile cam={camera} onOnline={vi.fn()} onClick={onClick} onConfigureZone={onConfigureZone} />);

    fireEvent.click(screen.getByRole("button", { name: "Зона видимости камеры" }));

    expect(onConfigureZone).toHaveBeenCalledWith(camera);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("masks the tile with the saved zone", () => {
    render(<CameraTile cam={{ ...camera, visibility_zone: zone() }} onOnline={vi.fn()} />);

    expect(screen.getByTestId("camera-visibility-mask").querySelector("path")).not.toBeNull();
  });
});
