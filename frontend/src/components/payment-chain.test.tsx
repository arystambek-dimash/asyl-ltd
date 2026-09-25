import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PaymentChain, paidByMethod } from "./payment-chain";
import type { Order, Payment } from "@/lib/types";
import { makeMe } from "@/test-utils/factories";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка оплаты",
}));

const me = makeMe({
  username: "cashier",
  // Кассир: вносит и подтверждает — его оплата закрывается сразу.
  permissions: ["payments.create", "payments.confirm"],
});

const order = {
  id: 156,
  client_name: "Ерхан Тетрадь",
  client_phone: "87001234567",
  currency: "KZT",
  status: "shipped",
  total_amount: "707000",
  paid_total: "0",
  remaining_amount: "707000",
  payment_status: "unpaid",
  settlement_intent: "debt",
  payments: [],
} as unknown as Order;

describe("PaymentChain", () => {
  it("does not show a dead receive action for an automatic provider invoice", () => {
    const providerOrder = {
      ...order,
      pending_payments: [
        {
          id: 77,
          order: order.id,
          amount: "1000.00",
          method: "invoice",
          status: "requested",
          paid_at: "2026-08-16T10:00:00Z",
          confirmation_mode: "automatic",
        },
      ],
    } as unknown as Order;

    render(<PaymentChain order={providerOrder} me={me} onChanged={vi.fn()} />);

    expect(screen.getByText("Ожидаем подтверждение сервиса")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оплата поступила" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить получение" })).not.toBeInTheDocument();
  });
});

function payment(fields: Partial<Payment>): Payment {
  return {
    id: 1,
    order: 1,
    amount: "0.00",
    method: "cash",
    status: "confirmed",
    paid_at: "2026-07-26T00:00:00Z",
    method_label: fields.method === "kaspi" ? "QR" : "Наличные",
    ...fields,
  } as Payment;
}

function orderWith(payments: Payment[]): Order {
  return { currency: "KZT", payments } as Order;
}

describe("paidByMethod", () => {
  it("splits a mixed payment into its methods, largest first", () => {
    const rows = paidByMethod(
      orderWith([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi" }),
      ]),
    );
    expect(rows).toEqual([
      { currency: "KZT", method: "kaspi", label: "QR", amount: 400000 },
      { currency: "KZT", method: "cash", label: "Наличные", amount: 300000 },
    ]);
  });

  it("counts a refund against its own method so the split still equals paid_total", () => {
    const rows = paidByMethod(
      orderWith([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi", refunded_amount: "50000.00" }),
      ]),
    );
    expect(rows).toEqual([
      { currency: "KZT", method: "kaspi", label: "QR", amount: 350000 },
      { currency: "KZT", method: "cash", label: "Наличные", amount: 300000 },
    ]);
    expect(rows.reduce((sum, { amount }) => sum + amount, 0)).toBe(650000);
  });

  it("ignores payments the cashier has not confirmed yet", () => {
    const rows = paidByMethod(
      orderWith([
        payment({ id: 1, amount: "300000.00", method: "cash" }),
        payment({ id: 2, amount: "400000.00", method: "kaspi", status: "received" }),
      ]),
    );
    expect(rows).toEqual([{ currency: "KZT", method: "cash", label: "Наличные", amount: 300000 }]);
  });

  it("merges repeat payments made by the same method", () => {
    const rows = paidByMethod(
      orderWith([
        payment({ id: 1, amount: "100000.00", method: "cash" }),
        payment({ id: 2, amount: "200000.00", method: "cash" }),
      ]),
    );
    expect(rows).toEqual([{ currency: "KZT", method: "cash", label: "Наличные", amount: 300000 }]);
  });
});
