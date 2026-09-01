import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CameraFeed } from "@/components/camera-wall";
import { ShipmentLauncher } from "@/components/shipping/shipment-launcher";
import type { Order } from "@/lib/types";

const camera = {
  id: "camera-2",
  name: "Camera 2",
  zone: "Пост погрузки",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
} satisfies CameraFeed & { src: string };

const otherCamera = {
  ...camera,
  id: "camera-3",
  name: "Camera 3",
  zone: "Другой отдел",
  src: "cam3",
};

const freeCamera = {
  ...camera,
  id: "camera-4",
  name: "Camera 4",
  zone: "Свободный пост",
  src: "cam4",
};

const order: Order = {
  id: 401,
  client: 1,
  client_name: "Магнум",
  currency: "KZT",
  status: "confirmed",
  transport_type: "truck",
  truck_number: "",
  items: [],
  total_amount: "0.00",
  paid_total: "0.00",
  is_fully_paid: true,
  debt_override: false,
  created_at: "2026-08-15T00:00:00Z",
};

describe("ShipmentLauncher", () => {
  it("starts AI counting without a scale precondition", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<ShipmentLauncher orders={[order]} cameras={[camera]} onStart={onStart} />);

    await user.selectOptions(screen.getByLabelText("Заказ"), "401");
    await user.selectOptions(screen.getByLabelText("Камера"), "cam2");

    expect(
      screen.getByText(/подключит заказ только к готовому непрерывному AI-процессору camN\/sub/),
    ).toBeInTheDocument();
    expect(screen.getByText(/отдельный холодный счётчик не создаётся/)).toBeInTheDocument();
    expect(screen.queryByText(/входн.*вес|весы/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Начатьотгрузку" }));
    expect(onStart).toHaveBeenCalledWith(order, camera);
  });

  it("does not offer a camera while continuous AI policy is pending", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(
      <ShipmentLauncher
        orders={[order]}
        cameras={[camera]}
        continuousReady={false}
        continuousDetail="cam2 ещё прогревается"
        onStart={onStart}
      />,
    );

    await userEvent.setup().selectOptions(screen.getByLabelText("Заказ"), "401");
    expect(screen.getByText("cam2 ещё прогревается")).toBeInTheDocument();
    expect(screen.getByLabelText("Камера")).not.toHaveValue("cam2");
    expect(screen.getByRole("button", { name: "Начатьотгрузку" })).toBeDisabled();
    expect(onStart).not.toHaveBeenCalled();
  });

  it("uses per-shipment-camera readiness instead of unrelated AI 24/7 state", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(
      <ShipmentLauncher
        orders={[order]}
        cameras={[camera]}
        continuousReady
        cameraReadiness={{ cam2: { status: "pending", detail: "cam2: нет direct substream" } }}
        continuousDetail="cam2: нет direct substream"
        onStart={onStart}
      />,
    );

    await userEvent.setup().selectOptions(screen.getByLabelText("Заказ"), "401");
    expect(screen.getByText("cam2: нет direct substream")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Начатьотгрузку" })).toBeDisabled();
  });

  it("blocks globally active shipping sessions even when absent from the visible session list", async () => {
    render(
      <ShipmentLauncher
        orders={[order]}
        cameras={[camera, otherCamera, freeCamera]}
        busyCameras={["cam2"]}
        shippingProcessors={[
          {
            cam: "cam2",
            running: true,
            mode: "session",
            recording: true,
            total: 3,
          },
          {
            cam: "cam3",
            running: true,
            mode: "session",
            recording: true,
            total: 8,
          },
          {
            cam: "cam4",
            running: true,
            mode: "always_on",
            recording: false,
            total: 12,
          },
        ]}
        onStart={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await userEvent.setup().selectOptions(screen.getByLabelText("Заказ"), "401");
    const cameraSelect = screen.getByLabelText("Камера");
    expect(cameraSelect).not.toContainHTML('value="cam2"');
    expect(cameraSelect).not.toContainHTML('value="cam3"');
    expect(cameraSelect).toContainHTML('value="cam4"');
  });
});
