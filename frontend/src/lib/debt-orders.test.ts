import { describe, expect, it } from "vitest";
import type { Order } from "@/lib/types";
import { availableCents, blockingStore, moneyCents, pendingSum, remainingOf, type DebtStore } from "./debt-orders";

function order(patch: Record<string, unknown> = {}): Order {
  return {
    id: 1,
    client: 1,
    currency: "KZT",
    status: "shipped",
    truck_number: "",
    items: [],
    total_amount: "1000",
    paid_total: "200",
    ...patch,
  } as unknown as Order;
}

const store: DebtStore = {
  id: 5,
  name: "Мерей",
  payment_schedule_type: "weekly",
  payment_days: [1],
  window_open: false,
};

describe("debt orders", () => {
  it("reads the remaining amount with a legacy fallback", () => {
    expect(remainingOf(order({ remaining_amount: "750.50" }))).toBe(750.5);
    expect(remainingOf(order())).toBe(800);
  });

  it("sums reservations and converts money to cents", () => {
    const withPending = order({ pending_payments: [{ amount: "100.25" }, { amount: "50" }] });
    expect(pendingSum(withPending)).toBe(150.25);
    expect(moneyCents("12.34")).toBe(1234);
    expect(moneyCents("abc")).toBe(0);
  });

  it("subtracts reservations from what can still be taken", () => {
    expect(availableCents(order({ pending_payments: [{ amount: "300" }] }))).toBe(50000);
    expect(availableCents(order({ pending_payments: [{ amount: "900" }] }))).toBe(0);
  });

  it("blocks only orders of stores whose payment window is closed", () => {
    expect(blockingStore(order(), [store])).toBeNull();
    expect(blockingStore(order({ store: 5 }), [store])).toBe(store);
    expect(blockingStore(order({ store: 5 }), [{ ...store, window_open: true }])).toBeNull();
    expect(blockingStore(order({ store: 5 }), [{ ...store, payment_schedule_type: "none" }])).toBeNull();
    expect(blockingStore(order({ store: 9 }), [store])).toBeNull();
  });
});
