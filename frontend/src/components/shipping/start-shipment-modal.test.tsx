import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StartShipmentModal } from "@/components/shipping/start-shipment-modal";
import type { PlayableCamera } from "@/lib/shipping-cameras";
import type { Order } from "@/lib/types";

const camera: PlayableCamera = {
  id: "camera-2",
  name: "Camera 2",
  zone: "Пост погрузки",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
};
const otherCamera: PlayableCamera = { ...camera, id: "camera-3", name: "Camera 3", zone: "Другой пост", src: "cam3" };

const order: Order = {
  id: 401,
  client: 1,
  client_name: "Магнум",
  currency: "KZT",
  status: "confirmed",
  transport_type: "truck",
  truck_number: "123ABC02",
  items: [{ product: 1, product_label: "Мука 50 кг", quantity: 40 }],
  total_amount: "0.00",
  paid_total: "0.00",
  is_fully_paid: true,
  debt_override: false,
  created_at: "2026-08-15T00:00:00Z",
};

function renderModal(overrides: Partial<Parameters<typeof StartShipmentModal>[0]> = {}) {
  const onStart = vi.fn().mockResolvedValue({ ok: true, error: "" });
  const onClose = vi.fn();
  render(
    <StartShipmentModal
      order={order}
      cameras={[camera, otherCamera]}
      camerasBySrc={new Map([camera, otherCamera].map((item) => [item.src, item]))}
      availability={{ continuousReady: true }}
      onClose={onClose}
      onStart={onStart}
      {...overrides}
    />,
  );
  return { onStart, onClose };
}

describe("StartShipmentModal", () => {
  it("lets the operator pick a camera and starts the order on it", async () => {
    const user = userEvent.setup();
    const { onStart, onClose } = renderModal();

    expect(screen.getByRole("dialog", { name: "Начать погрузку?" })).toBeInTheDocument();
    expect(screen.getByText("40 меш. по заказу")).toBeInTheDocument();
    expect(screen.getByText("Мука 50 кг")).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Начать погрузку" });
    expect(start).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Камера"), "cam3");
    await user.click(start);

    expect(onStart).toHaveBeenCalledWith(order, "cam3");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("fixes the kiosk camera without a picker", async () => {
    const user = userEvent.setup();
    const { onStart } = renderModal({ cameraLocked: true, kioskCamera: "cam2" });

    expect(screen.queryByLabelText("Камера")).not.toBeInTheDocument();
    expect(screen.getByText("Пост погрузки")).toBeInTheDocument();
    expect(screen.getByText("· закреплена")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Начать погрузку" }));
    expect(onStart).toHaveBeenCalledWith(order, "cam2");
  });

  it("keeps the loading camera of an order that already has one", () => {
    renderModal({
      order: { ...order, status: "loading", loading_camera: "cam3" },
      availability: { continuousReady: true, cameraOwners: { cam3: 401 } },
    });

    expect(screen.queryByLabelText("Камера")).not.toBeInTheDocument();
    expect(screen.getByText("Другой пост")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Начать погрузку" })).toBeEnabled();
  });

  it("stays disabled without a ready camera and explains why", () => {
    renderModal({
      cameras: [camera],
      availability: { continuousReady: false },
      continuousDetail: "cam2 ещё прогревается",
    });

    expect(screen.getByRole("button", { name: "Начать погрузку" })).toBeDisabled();
    expect(screen.getByLabelText("Камера")).toBeDisabled();
    expect(screen.getByRole("option", { name: "Камеры отгрузки ещё не готовы" })).toBeInTheDocument();
    expect(screen.getByText("cam2 ещё прогревается")).toBeInTheDocument();
  });

  it("stays disabled when no cameras are configured", () => {
    renderModal({ cameras: [], camerasBySrc: new Map() });

    expect(screen.getByRole("button", { name: "Начать погрузку" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Камеры не настроены" })).toBeInTheDocument();
  });

  it("shows a failed start inside the modal", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal({
      cameraLocked: true,
      kioskCamera: "cam2",
      onStart: vi.fn().mockResolvedValue({ ok: false, error: "ПК камер не отвечает" }),
    });

    await user.click(screen.getByRole("button", { name: "Начать погрузку" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ПК камер не отвечает");
    expect(screen.getByRole("dialog", { name: "Начать погрузку?" })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
