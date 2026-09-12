import { describe, expect, it } from "vitest";
import type { Order, Payment } from "@/lib/types";
import {
  INITIAL_POS_STATE,
  appendDigit,
  eraseDigit,
  paymentOutcome,
  posOrderBlock,
  posReducer,
  wholeTengeLimit,
  type PosState,
} from "./pos-logic";

function order(patch: Record<string, unknown> = {}): Order {
  return {
    id: 130,
    client: 1,
    currency: "KZT",
    status: "shipped",
    truck_number: "",
    items: [],
    total_amount: "195840.50",
    paid_total: "0",
    remaining_amount: "195840.50",
    pending_payments: [],
    ...patch,
  } as unknown as Order;
}

function payment(patch: Record<string, unknown> = {}): Payment {
  return {
    id: 501,
    order: 130,
    amount: "100.00",
    currency: "KZT",
    method: "kaspi",
    status: "requested",
    paid_at: "2026-09-12T10:00:00",
    recorded_by: 1,
    provider: null,
    ...patch,
  } as unknown as Payment;
}

describe("amount keypad", () => {
  it("never starts with zero and never exceeds the limit", () => {
    expect(appendDigit("", "0", 1000)).toBe("");
    expect(appendDigit("", "5", 1000)).toBe("5");
    expect(appendDigit("12", "3", 1000)).toBe("123");
    expect(appendDigit("999", "9", 1000)).toBe("999");
    expect(appendDigit("12", "x", 1000)).toBe("12");
    expect(eraseDigit("123")).toBe("12");
    expect(eraseDigit("")).toBe("");
  });

  it("splits what QR can take into whole tenge and leftover tiyn", () => {
    expect(wholeTengeLimit(order())).toEqual({ max: 195840, tiyn: 50 });
    expect(wholeTengeLimit(order({ pending_payments: [{ amount: "195840.50" }] }))).toEqual({ max: 0, tiyn: 0 });
  });
});

describe("posOrderBlock", () => {
  it("explains why an order cannot be paid by QR", () => {
    expect(posOrderBlock(order({ currency: "USD" }), [])).toBe("QR только в тенге");
    expect(
      posOrderBlock(order({ store: 5 }), [
        { id: 5, name: "Мерей", payment_schedule_type: "weekly", payment_days: [1], window_open: false },
      ]),
    ).toBe("Оплата для магазина «Мерей» сегодня недоступна");
    expect(posOrderBlock(order({ pending_payments: [{ amount: "195840.50" }] }), [])).toBe(
      "Всё уже ожидает подтверждения",
    );
    expect(posOrderBlock(order({ remaining_amount: "0.40" }), [])).toBe("Нечего оплачивать");
    expect(posOrderBlock(order(), [])).toBeNull();
  });
});

describe("paymentOutcome", () => {
  it("maps payment and provider states", () => {
    expect(paymentOutcome(payment({ status: "confirmed" }))).toBe("paid");
    expect(paymentOutcome(payment({ status: "rejected" }))).toBe("failed");
    expect(paymentOutcome(payment({ provider: { status: "expired" } }))).toBe("failed");
    expect(paymentOutcome(payment({ provider: { status: "pending" } }))).toBe("waiting");
  });
});

describe("posReducer", () => {
  const picked: PosState = posReducer(posReducer(INITIAL_POS_STATE, { type: "client", id: 1, name: "Асан" }), {
    type: "order",
    id: 130,
    amount: "195840",
  });

  it("walks client → order → amount and back", () => {
    expect(picked).toMatchObject({ step: "amount", clientId: 1, clientName: "Асан", orderId: 130, amount: "195840" });
    const toOrder = posReducer(picked, { type: "back" });
    expect(toOrder).toMatchObject({ step: "order", orderId: null, amount: "" });
    expect(posReducer(toOrder, { type: "back" })).toMatchObject({ step: "client", clientId: null });
    expect(posReducer(INITIAL_POS_STATE, { type: "back" })).toBe(INITIAL_POS_STATE);
  });

  it("keeps the selection when switching payment modes before issuing", () => {
    const remote = posReducer(picked, { type: "flow", flow: "remote" });
    expect(remote).toMatchObject({ flow: "remote", step: "amount", orderId: 130 });
    const phone = posReducer(remote, { type: "phone-step", phone: "87011234567" });
    expect(phone).toMatchObject({ step: "phone", phone: "87011234567" });
    expect(posReducer(phone, { type: "flow", flow: "qr" })).toMatchObject({ flow: "qr", step: "amount" });
    expect(posReducer(phone, { type: "flow", flow: "remote" })).toBe(phone);
  });

  it("starts over when the mode changes after a QR was issued", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    expect(issued.step).toBe("result");
    expect(posReducer(issued, { type: "flow", flow: "qr" })).toBe(issued);
    expect(posReducer(issued, { type: "flow", flow: "remote" })).toMatchObject({
      flow: "remote",
      step: "client",
      payment: null,
    });
  });

  it("updates only the issued payment, retries from the amount and resets to a new client", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    expect(posReducer(issued, { type: "payment", payment: payment({ id: 999, status: "confirmed" }) })).toBe(issued);
    expect(posReducer(issued, { type: "payment", payment: payment({ status: "confirmed" }) }).payment?.status).toBe(
      "confirmed",
    );
    expect(posReducer(issued, { type: "retry" })).toMatchObject({ step: "amount", payment: null, orderId: 130 });
    expect(posReducer(issued, { type: "reset" })).toMatchObject({ step: "client", clientId: null, flow: "qr" });
  });

  it("clears the error on the next edit", () => {
    const failed = posReducer(picked, { type: "error", error: "Сервис недоступен" });
    expect(failed.error).toBe("Сервис недоступен");
    expect(posReducer(failed, { type: "amount", amount: "10" }).error).toBe("");
  });

  it("forgets the previous client's phone when another client is picked", () => {
    const typed = posReducer({ ...picked, step: "phone", phone: "87011234567" }, { type: "back" });
    expect(typed.phone).toBe("87011234567");
    expect(posReducer(typed, { type: "client", id: 2, name: "Мерей" }).phone).toBe("");
  });

  it("clears a status-check error once a fresh payment state arrives", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    const failed = posReducer(issued, { type: "error", error: "Нет сети" });
    expect(posReducer(failed, { type: "payment", payment: payment() }).error).toBe("");
  });

  it("applies a poll error only to the payment on screen", () => {
    const issued = posReducer(picked, { type: "issued", payment: payment() });
    expect(posReducer(issued, { type: "poll-error", paymentId: 501, error: "Нет сети" }).error).toBe("Нет сети");
    expect(posReducer(issued, { type: "poll-error", paymentId: 999, error: "Нет сети" })).toBe(issued);
    const fresh = posReducer(issued, { type: "reset" });
    expect(posReducer(fresh, { type: "poll-error", paymentId: 501, error: "Нет сети" })).toBe(fresh);
  });
});
