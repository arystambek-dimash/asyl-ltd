import { describe, expect, it, vi } from "vitest";
import type { Order } from "@/lib/types";
import { allowedShippingBoardStages, postLoadedOrderExit, shippingBoardAction } from "@/lib/shipping-flow";

function order(status: string, transportType: Order["transport_type"] = "truck"): Order {
  return {
    id: 401,
    client: 1,
    currency: "KZT",
    status,
    transport_type: transportType,
    truck_number: "",
    items: [],
    total_amount: "0.00",
    paid_total: "0.00",
    is_fully_paid: true,
    debt_override: false,
    created_at: "2026-08-15T00:00:00Z",
  };
}

const noCapabilities = {
  canLoad: false,
  canTrain: false,
  canShip: false,
  canRollback: false,
};

describe("shipping board flow", () => {
  it("keeps loaded separate from shipped and requires shipping.ship", () => {
    const loaded = order("loaded");

    expect(allowedShippingBoardStages(loaded, noCapabilities)).toEqual([]);
    expect(allowedShippingBoardStages(loaded, { ...noCapabilities, canShip: true })).toEqual(["done"]);
    expect(shippingBoardAction(loaded, "done")).toBe("ship");
  });

  it("maps loading completion to the ready-for-exit stage, never directly to shipped", () => {
    const loading = order("loading");

    expect(allowedShippingBoardStages(loading, { ...noCapabilities, canLoad: true })).toEqual(["waiting", "exit"]);
    expect(shippingBoardAction(loading, "exit")).toBe("finish_loading");
    expect(shippingBoardAction(loading, "done")).toBeNull();
  });

  it("does not offer an impossible finish action for an arrived train", () => {
    const arrivedTrain = order("arrived", "train");

    expect(allowedShippingBoardStages(arrivedTrain, { ...noCapabilities, canTrain: true })).toEqual([]);
    expect(shippingBoardAction(arrivedTrain, "exit")).toBeNull();
  });

  it("does not give a loader permission to ship a loaded order", () => {
    const loaded = order("loaded");

    expect(allowedShippingBoardStages(loaded, { ...noCapabilities, canLoad: true })).toEqual([]);
  });

  it("uses only the dedicated ship endpoint for the loaded exit", async () => {
    const post = vi.fn().mockResolvedValue(undefined);

    await postLoadedOrderExit(401, post);

    expect(post).toHaveBeenCalledOnce();
    expect(post).toHaveBeenCalledWith("/orders/401/ship/", {});
  });
});
