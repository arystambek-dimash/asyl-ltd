import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CameraFeed } from "@/components/camera-wall";
import { ShipmentLauncher } from "@/components/shipping/shipment-launcher";
import type { Order } from "@/lib/types";

const camera = {
  id: "camera-2",
  name: "Camera 2",
  zone: "Конвейер",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
  conveyor: {
    configured: true,
    online: true,
    state: "off",
    desired: 0,
    feedback: 0,
    error: null,
  },
} satisfies CameraFeed & { src: string };

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
  it("describes only the AI/ESP32 camera flow without a scale precondition", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<ShipmentLauncher orders={[order]} cameras={[camera]} onStart={onStart} />);

    await user.selectOptions(screen.getByLabelText("Заказ"), "401");
    await user.selectOptions(screen.getByLabelText("Камера"), "cam2");

    expect(screen.getByText(/Моноблок запустит AI и ESP32 для выбранных заказа и камеры/)).toBeInTheDocument();
    expect(screen.queryByText(/входн.*вес|весы/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Начатьотгрузку" }));
    expect(onStart).toHaveBeenCalledWith(order, camera);
  });

  it("starts AI when the selected camera has no ESP32 configured", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);
    const aiOnlyCamera = { ...camera, conveyor: undefined } as CameraFeed & { src: string };
    render(<ShipmentLauncher orders={[order]} cameras={[aiOnlyCamera]} onStart={onStart} />);

    await user.selectOptions(screen.getByLabelText("Заказ"), "401");
    await user.selectOptions(screen.getByLabelText("Камера"), "cam2");

    expect(screen.getByText(/ESP32 не требуется: Моноблок запустит AI/)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Начатьотгрузку" });
    expect(start).toBeEnabled();
    await user.click(start);

    expect(onStart).toHaveBeenCalledWith(order, aiOnlyCamera);
  });
});
