import { describe, expect, it } from "vitest";
import { ORDER_MANUAL_STATUSES, ORDER_PUBLIC_STATUSES, orderStatusGroup, orderStatusLabel } from "@/lib/constants";

describe("order status presentation", () => {
  it("keeps completed loading separate from physical exit", () => {
    expect(orderStatusGroup("loaded")).toBe("loaded");
    expect(orderStatusLabel("loaded")).toBe("Готов к выезду");
    expect(orderStatusGroup("shipped")).toBe("shipped");
    expect(ORDER_PUBLIC_STATUSES).toContain("loaded");
  });

  it("does not expose loaded as a manual status override", () => {
    expect(ORDER_MANUAL_STATUSES).not.toContain("loaded");
  });
});
