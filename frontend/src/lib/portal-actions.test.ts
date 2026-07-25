import { describe, expect, it } from "vitest";
import { clientStep } from "./portal-actions";

describe("clientStep", () => {
  it("keeps payment controls visible while a settled order has a payable replacement", () => {
    expect(clientStep("shipped", "settled", true)).toBe("pay");
  });

  it("finishes a settled order after every extra payment is closed", () => {
    expect(clientStep("shipped", "settled", false)).toBe("done");
  });

  it("keeps an unpaid shipped order in payment", () => {
    expect(clientStep("shipped", "partial", false)).toBe("pay");
  });
});
