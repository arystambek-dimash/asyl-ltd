import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShippingTransportCamera } from "./shipping-transport-camera";
import type { ShippingTransportCameraSettings } from "@/lib/types";

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: mocks,
  apiError: (cause: unknown) => (cause instanceof Error ? cause.message : "Ошибка API"),
  isCanceledRequest: () => false,
}));
vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="number-preview">{src}</div>,
  ensureCameraStreamToken: vi.fn(),
}));
vi.mock("@/lib/use-video-box", () => ({ useVideoBox: () => ({ left: 0, top: 0, width: 640, height: 360 }) }));

const url = "/cameras/cam1/transport-camera/";
const unset: ShippingTransportCameraSettings = {
  conveyor_camera: "cam1",
  number_camera: null,
  recognition_model: null,
  updated_at: null,
};
const configured: ShippingTransportCameraSettings = {
  conveyor_camera: "cam1",
  number_camera: "cam2",
  recognition_model: "vehicle_number",
  updated_at: "2026-09-07T12:00:00Z",
};
const cameras = [
  { id: "1", name: "cam1", zone: "Конвейер", src: "cam1", kind: "direct", online: true },
  { id: "2", name: "cam2", zone: "Въезд грузовиков", src: "cam2", kind: "direct", online: true },
  { id: "3", name: "cam3", zone: "Номер вагона", src: "cam3", kind: "direct", online: true },
  { id: "4", name: "cam_mac", zone: "Другая модель", src: "cam_mac", kind: "direct", online: true },
  { id: "5", name: "locked", zone: "Недоступная", src: null, kind: "locked", online: false },
];

function serveSettings(value = configured) {
  mocks.get.mockImplementation((requestUrl: string) =>
    Promise.resolve({ data: requestUrl === "/cameras/" ? cameras : value }),
  );
}

async function ready() {
  await screen.findByLabelText("Камера номера");
}

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  serveSettings();
  mocks.delete.mockResolvedValue({ status: 204 });
  mocks.post.mockResolvedValue({ data: { ...configured, number: "123ABC02", observed_at: "2026-09-07T12:30:00Z" } });
});

describe("ShippingTransportCamera", () => {
  it("requires explicit camera and model selections and previews the main stream", async () => {
    serveSettings(unset);
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    expect(screen.getByLabelText("Модель распознавания")).toHaveValue("");
    expect(screen.queryByRole("option", { name: "Конвейер · cam1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Другая модель/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить связь" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Камера номера"), "cam3");
    await user.selectOptions(screen.getByLabelText("Модель распознавания"), "wagon_number");
    expect(screen.getByTestId("number-preview")).toHaveTextContent("cam3main");
    expect(screen.getByRole("button", { name: "Сохранить связь" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Проверить распознавание" })).toBeDisabled();
  });

  it("saves exactly once while busy and tests only the confirmed configuration", async () => {
    const user = userEvent.setup();
    let resolveSave: (response: { data: ShippingTransportCameraSettings }) => void = () => undefined;
    mocks.put.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.selectOptions(screen.getByLabelText("Камера номера"), "cam3");
    await user.selectOptions(screen.getByLabelText("Модель распознавания"), "wagon_number");
    const save = screen.getByRole("button", { name: "Сохранить связь" });
    act(() => {
      fireEvent.click(save);
      fireEvent.click(save);
    });
    expect(mocks.put).toHaveBeenCalledTimes(1);
    expect(mocks.put).toHaveBeenCalledWith(url, {
      number_camera: "cam3",
      recognition_model: "wagon_number",
      loading_zone: null,
    });
    expect(screen.getByLabelText("Камера номера")).toBeDisabled();
    await act(async () =>
      resolveSave({ data: { ...configured, number_camera: "cam3", recognition_model: "wagon_number" } }),
    );
    expect(screen.getByText("Связь сохранена")).toBeInTheDocument();
    mocks.post.mockResolvedValueOnce({
      data: {
        ...configured,
        number_camera: "cam3",
        recognition_model: "wagon_number",
        number: "00123456",
        observed_at: "2026-09-07T12:30:00Z",
      },
    });
    await user.click(screen.getByRole("button", { name: "Проверить распознавание" }));
    expect(await screen.findByText("00123456")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledWith(`${url}recognize/`, {}, { signal: expect.any(AbortSignal) });
    expect(screen.getByText("Проверка номера не запускает погрузку.")).toBeInTheDocument();
  });

  it("keeps a failed save visible and preserves the draft for retry", async () => {
    mocks.put.mockRejectedValueOnce(new Error("Камера уже закреплена за другим конвейером"));
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.selectOptions(screen.getByLabelText("Камера номера"), "cam3");
    await user.click(screen.getByRole("button", { name: "Сохранить связь" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Камера уже закреплена");
    expect(screen.getByLabelText("Камера номера")).toHaveValue("cam3");
    expect(screen.getByRole("button", { name: "Сохранить связь" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Проверить распознавание" })).toBeDisabled();
  });

  it("loads the saved zone and sends normalized edited boundaries", async () => {
    serveSettings({ ...configured, loading_zone: [0.1, 0.2, 0.8, 0.9] });
    mocks.put.mockImplementation((_url, body) => Promise.resolve({ data: { ...configured, ...body } }));
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    expect(screen.getByLabelText("Область наблюдения")).toHaveValue("zone");
    expect(screen.getByLabelText("Слева, %")).toHaveValue(10);
    expect(screen.getByRole("button", { name: "Сохранить связь" })).toBeDisabled();
    await user.clear(screen.getByLabelText("Слева, %"));
    await user.type(screen.getByLabelText("Слева, %"), "25");
    await user.click(screen.getByRole("button", { name: "Сохранить связь" }));
    expect(mocks.put).toHaveBeenCalledWith(url, {
      number_camera: "cam2",
      recognition_model: "vehicle_number",
      loading_zone: [0.25, 0.2, 0.8, 0.9],
    });
    await screen.findByText("Связь сохранена");
    await user.selectOptions(screen.getByLabelText("Область наблюдения"), "full");
    await user.click(screen.getByRole("button", { name: "Сохранить связь" }));
    expect(mocks.put).toHaveBeenLastCalledWith(url, {
      number_camera: "cam2",
      recognition_model: "vehicle_number",
      loading_zone: null,
    });
  });

  it("blocks inverted zones and clears a zone when the source camera changes", async () => {
    serveSettings({ ...configured, loading_zone: [0.1, 0.2, 0.8, 0.9] });
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    fireEvent.change(screen.getByLabelText("Слева, %"), { target: { value: "90" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Левая граница должна быть меньше правой");
    expect(screen.getByRole("button", { name: "Сохранить связь" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Проверить распознавание" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Камера номера"), "cam3");
    expect(screen.getByLabelText("Область наблюдения")).toHaveValue("full");
    expect(screen.queryByLabelText("Слева, %")).not.toBeInTheDocument();
  });

  it("keeps the zone rectangular when an overlay corner is moved", async () => {
    serveSettings({ ...configured, loading_zone: [0.1, 0.2, 0.8, 0.9] });
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    fireEvent.keyDown(screen.getByRole("button", { name: "Угол зоны 2" }), { key: "ArrowLeft" });
    expect(screen.getByLabelText("Справа, %")).toHaveValue(79.5);
    expect(screen.getByLabelText("Слева, %")).toHaveValue(10);
    expect(screen.getByTestId("vehicle-roi-polygon").querySelector("polygon")).toHaveAttribute(
      "points",
      "100,200 795,200 795,900 100,900",
    );
  });

  it("deletes the saved link and clears recognition and preview state", async () => {
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.click(screen.getByRole("button", { name: "Проверить распознавание" }));
    await screen.findByText("123ABC02");
    await user.click(screen.getByRole("button", { name: "Удалить связь" }));
    expect(await screen.findByText("Связь удалена")).toBeInTheDocument();
    expect(mocks.delete).toHaveBeenCalledWith(url);
    expect(screen.getByLabelText("Камера номера")).toHaveValue("");
    expect(screen.queryByTestId("number-preview")).not.toBeInTheDocument();
    expect(screen.queryByText("123ABC02")).not.toBeInTheDocument();
  });

  it("reports a missing number without inventing a successful match", async () => {
    mocks.post.mockResolvedValueOnce({ data: { ...configured, number: null, observed_at: "2026-09-07T12:30:00Z" } });
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.click(screen.getByRole("button", { name: "Проверить распознавание" }));
    expect(await screen.findByText("Номер не распознан")).toBeInTheDocument();
  });

  it("rejects recognition from a concurrently changed camera mapping", async () => {
    mocks.post.mockResolvedValueOnce({
      data: { ...configured, number_camera: "cam3", number: "00123456", observed_at: "2026-09-07T12:30:00Z" },
    });
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.click(screen.getByRole("button", { name: "Проверить распознавание" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Связь камеры изменилась");
    expect(screen.queryByText("00123456")).not.toBeInTheDocument();
  });

  it("shows and retries initial API errors", async () => {
    mocks.get.mockImplementation((requestUrl: string) =>
      requestUrl === "/cameras/"
        ? Promise.resolve({ data: cameras })
        : Promise.reject(new Error("Нет связи с сервером")),
    );
    const user = userEvent.setup();
    render(<ShippingTransportCamera conveyorCamera="cam1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
    serveSettings();
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    await ready();
    expect(screen.getByLabelText("Камера номера")).toHaveValue("cam2");
  });

  it("aborts recognition and discards late responses when a conveyor is closed", async () => {
    let signal: AbortSignal | undefined;
    let resolveTest: (response: unknown) => void = () => undefined;
    mocks.post.mockImplementation((_url, _body, options) => {
      signal = options.signal;
      return new Promise((resolve) => {
        resolveTest = resolve;
      });
    });
    const user = userEvent.setup();
    const view = render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await ready();
    await user.click(screen.getByRole("button", { name: "Проверить распознавание" }));
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      resolveTest({ data: { ...configured, number: "123ABC02", observed_at: "2026-09-07T12:30:00Z" } }),
    );
    expect(screen.queryByText("123ABC02")).not.toBeInTheDocument();
  });

  it("aborts a previous read and does not leak its mapping into another conveyor", async () => {
    let oldSignal: AbortSignal | undefined;
    let resolveRead: (response: { data: ShippingTransportCameraSettings }) => void = () => undefined;
    mocks.get.mockImplementation((requestUrl, options) => {
      if (requestUrl === "/cameras/") return Promise.resolve({ data: cameras });
      if (requestUrl === url) {
        oldSignal = options.signal;
        return new Promise((resolve) => {
          resolveRead = resolve;
        });
      }
      return Promise.resolve({ data: { ...unset, conveyor_camera: "cam3" } });
    });
    const view = render(<ShippingTransportCamera conveyorCamera="cam1" />);
    await waitFor(() => expect(oldSignal).toBeDefined());
    view.rerender(<ShippingTransportCamera conveyorCamera="cam3" />);
    await ready();
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => resolveRead({ data: configured }));
    expect(screen.getByLabelText("Камера номера")).toHaveValue("");
  });
});
