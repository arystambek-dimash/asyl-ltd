import { describe, expect, it } from "vitest";
import { cameraOwnersFor, isAiOnlineStatus } from "@/lib/shipping-cameras";
import type { AiCountingSession, Order } from "@/lib/types";

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
