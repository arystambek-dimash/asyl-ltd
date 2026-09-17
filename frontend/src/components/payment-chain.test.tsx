import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PaymentChain } from "./payment-chain";
import type { Me, Order } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка оплаты",
}));

const me = {
  id: 1,
  username: "cashier",
  // Кассир: вносит и подтверждает — его оплата закрывается сразу.
  permissions: ["payments.create", "payments.confirm"],
  is_superuser: false,
} as unknown as Me;

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
    expect(screen.queryByRole("button", { name: "Отметить получение" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить получение" })).not.toBeInTheDocument();
  });
});
