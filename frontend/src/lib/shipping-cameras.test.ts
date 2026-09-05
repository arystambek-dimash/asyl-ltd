import { describe, expect, it } from "vitest";
import {
  availableCamerasForOrder,
  cameraOwnersFor,
  cameraPlaceholder,
  isAiOnlineStatus,
  type PlayableCamera,
} from "@/lib/shipping-cameras";
import type { AiCountingSession, Order } from "@/lib/types";

const camera: PlayableCamera = {
  id: "camera-2",
  name: "Camera 2",
  zone: "Пост погрузки",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
};
const otherCamera: PlayableCamera = { ...camera, id: "camera-3", name: "Camera 3", zone: "Другой отдел", src: "cam3" };
const freeCamera: PlayableCamera = { ...camera, id: "camera-4", name: "Camera 4", zone: "Свободный пост", src: "cam4" };

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

describe("availableCamerasForOrder", () => {
  it("offers a ready online camera and starts without a scale precondition", () => {
    const available = availableCamerasForOrder(order, [camera], {});
    expect(available).toEqual([camera]);
    expect(cameraPlaceholder([camera], available, {})).toBe("Выберите камеру");
  });

  it("does not offer a camera while continuous AI policy is pending", () => {
    const context = { continuousReady: false };
    const available = availableCamerasForOrder(order, [camera], context);
    expect(available).toEqual([]);
    expect(cameraPlaceholder([camera], available, context)).toBe("Камеры отгрузки ещё не готовы");
  });

  it("uses per-shipment-camera readiness instead of unrelated AI 24/7 state", () => {
    const context = {
      continuousReady: true,
      cameraReadiness: { cam2: { status: "pending" as const, detail: "cam2: нет direct substream" } },
    };
    const available = availableCamerasForOrder(order, [camera], context);
    expect(available).toEqual([]);
    expect(cameraPlaceholder([camera], available, context)).toBe("Камеры отгрузки ещё не готовы");
  });

  it("requires a confirmed online source", () => {
    const offline = { ...camera, online: false };
    const available = availableCamerasForOrder(order, [offline], {});
    expect(available).toEqual([]);
    expect(cameraPlaceholder([offline], available, {})).toBe("Нет камер онлайн");
  });

  it("blocks globally active shipping sessions even when absent from the visible session list", () => {
    const context = {
      busyCameras: ["cam2"],
      shippingProcessors: [
        { cam: "cam2", running: true, mode: "session" as const, recording: true, total: 3 },
        { cam: "cam3", running: true, mode: "session" as const, recording: true, total: 8 },
        { cam: "cam4", running: true, mode: "always_on" as const, recording: false, total: 12 },
      ],
    };
    const available = availableCamerasForOrder(order, [camera, otherCamera, freeCamera], context);
    expect(available.map((item) => item.src)).toEqual(["cam4"]);
  });

  it("keeps a camera bound to the order available and hides cameras bound to others", () => {
    const context = { busyCameras: ["cam2"], cameraOwners: { cam2: 401, cam3: 402 } };
    expect(availableCamerasForOrder(order, [camera, otherCamera], context).map((item) => item.src)).toEqual(["cam2"]);
    expect(availableCamerasForOrder({ id: 402 }, [camera, otherCamera], context).map((item) => item.src)).toEqual([
      "cam3",
    ]);
    expect(cameraPlaceholder([otherCamera], [], context)).toBe("Нет свободных камер");
    expect(cameraPlaceholder([], [], context)).toBe("Камеры не настроены");
  });
});

describe("isAiOnlineStatus", () => {
  it("accepts both the new and the legacy processor status", () => {
    expect(isAiOnlineStatus("online")).toBe(true);
    expect(isAiOnlineStatus(" Онлайн ")).toBe(true);
    expect(isAiOnlineStatus("запуск...")).toBe(false);
    expect(isAiOnlineStatus(undefined)).toBe(false);
  });
});

describe("cameraOwnersFor", () => {
  it("prefers live sessions over the order's loading camera", () => {
    const session = { id: 1, order_id: 500, camera: "cam2" } as AiCountingSession;
    const owners = cameraOwnersFor(
      [
        { ...order, id: 401, status: "loading", loading_camera: "cam2" },
        { ...order, id: 402, status: "shipped", loading_camera: "cam3" },
      ],
      [session],
    );
    expect(owners).toEqual({ cam2: 500 });
  });
});
