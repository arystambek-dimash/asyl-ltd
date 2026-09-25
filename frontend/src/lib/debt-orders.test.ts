import { describe, expect, it } from "vitest";
import type { Order } from "@/lib/types";
import { makeOrder, makePayment } from "@/test-utils/factories";
import {
  availableCents,
  blockingStore,
  moneyCents,
  pendingSum,
  remainingOf,
  storeBlockReason,
  type DebtStore,
} from "./debt-orders";

const order = (fields: Partial<Order> = {}) =>
  makeOrder({ status: "shipped", total_amount: "1000", paid_total: "200", remaining_amount: "800", ...fields });

const store: DebtStore = {
  id: 5,
  name: "Мерей",
  payment_schedule_type: "weekly",
  payment_days: [1],
  window_open: false,
};

describe("debt orders", () => {
  it("reads the remaining amount from the server field", () => {
    expect(remainingOf(order({ remaining_amount: "750.50" }))).toBe(750.5);
  });

  it("sums reservations and converts money to cents", () => {
    const withPending = order({ pending_payments: [makePayment({ amount: "100.25" }), makePayment({ amount: "50" })] });
    expect(pendingSum(withPending)).toBe(150.25);
    expect(moneyCents("12.34")).toBe(1234);
    expect(moneyCents("abc")).toBe(0);
  });

  it("subtracts reservations from what can still be taken", () => {
    expect(availableCents(order({ pending_payments: [makePayment({ amount: "300" })] }))).toBe(50000);
    expect(availableCents(order({ pending_payments: [makePayment({ amount: "900" })] }))).toBe(0);
  });

  it("blocks only orders of stores whose payment window is closed", () => {
    expect(blockingStore(order(), [store])).toBeNull();
    expect(blockingStore(order({ store: 5 }), [store])).toBe(store);
    expect(blockingStore(order({ store: 5 }), [{ ...store, window_open: true }])).toBeNull();
    expect(
      blockingStore(order({ store: 5 }), [{ ...store, payment_schedule_type: "none", window_open: true }]),
    ).toBeNull();
    expect(blockingStore(order({ store: 9 }), [store])).toBeNull();
  });

  it("lets a store prepay any day: the payment window schedules only debt of shipped orders", () => {
    expect(blockingStore(order({ store: 5, status: "confirmed" }), [store])).toBeNull();
    expect(blockingStore(order({ store: 5, status: "loaded" }), [store])).toBeNull();
  });

  it("explains a closed payment window with the store schedule", () => {
    expect(storeBlockReason(store)).toBe("Магазин «Мерей» платит по расписанию: Дни: Пн");
    expect(storeBlockReason({ ...store, payment_schedule_type: "monthly", payment_days: [10, 25] })).toBe(
      "Магазин «Мерей» платит по расписанию: Числа: 10, 25",
    );
  });
});
