import { describe, expect, it } from "vitest";
import { cameraOwnersFor, isLogicalCamera } from "@/lib/shipping-cameras";
import type { AiCountingSession } from "@/lib/types";
import { makeOrder } from "@/test-utils/factories";

const order = makeOrder();

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

describe("isLogicalCamera", () => {
  it("пропускает только логические камеры camN", () => {
    expect(["cam1", "cam12"].map(isLogicalCamera)).toEqual([true, true]);
    expect(["cam0", "cam_8c26", "cam1_sub", "nvr1"].map(isLogicalCamera)).toEqual([false, false, false, false]);
  });
});
