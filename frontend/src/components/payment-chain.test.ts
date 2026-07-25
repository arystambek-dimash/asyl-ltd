import { describe, expect, it } from "vitest";
import { paidByMethod } from "./payment-chain";
import type { Order, Payment } from "@/lib/types";

function payment(fields: Partial<Payment>): Payment {
  return {
    id: 1,
    order: 1,
    amount: "0.00",
    method: "cash",
    status: "confirmed",
    paid_at: "2026-07-26T00:00:00Z",
    ...fields,
  } as Payment;
}

function order(payments: Payment[]): Order {
  return { currency: "KZT", payments } as Order;
}

describe("paidByMethod", () => {
  it("splits a mixed payment into its methods, largest first", () => {
    const rows = paidByMethod(
      order([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi" }),
      ]),
    );
    expect(rows).toEqual([
      ["kaspi", 400000],
      ["cash", 300000],
    ]);
  });

  it("counts a refund against its own method so the split still equals paid_total", () => {
    const rows = paidByMethod(
      order([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi", refunded_amount: "50000.00" }),
      ]),
    );
    expect(rows).toEqual([
      ["kaspi", 350000],
      ["cash", 300000],
    ]);
    expect(rows.reduce((sum, [, amount]) => sum + amount, 0)).toBe(650000);
  });

  it("ignores payments the cashier has not confirmed yet", () => {
    const rows = paidByMethod(
      order([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi", status: "received" }),
      ]),
    );
    expect(rows).toEqual([["cash", 300000]]);
  });

  it("merges repeat payments made by the same method", () => {
    const rows = paidByMethod(
      order([
        payment({ id: 1, amount: "100000.00", method: "cash" }),
        payment({ id: 2, amount: "200000.00", method: "cash" }),
      ]),
    );
    expect(rows).toEqual([["cash", 300000]]);
  });
});
