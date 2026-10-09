import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Payment } from "@/lib/types";
import { TransactionDetail } from "./transaction-detail";

type Refund = NonNullable<Payment["refunds"]>[number];

function refund(id: number, status: Refund["status"], amount: string): Refund {
  return {
    id,
    amount,
    method: "cash",
    status,
    reason: "Возврат товара №21",
    requested_by_name: "Иван Петров",
    completed_at: null,
    created_at: "2026-10-09T10:00:00+05:00",
  };
}

describe("TransactionDetail", () => {
  it("выплата, отменённая исправлением возврата, — не «Ошибка»", () => {
    const payment = {
      id: 7,
      order: 3,
      currency: "KZT",
      amount: "10000.00",
      status: "confirmed",
      client_name: "Нуржан Сарыагаш",
      refunds: [refund(1, "cancelled", "3000.00"), refund(2, "completed", "2000.00"), refund(3, "failed", "500.00")],
    } as Payment;
    render(<TransactionDetail payment={payment} />);

    const cancelled = screen.getByText("Отменён (исправление возврата)").parentElement!;
    expect(cancelled).toHaveTextContent(/3\s000/);
    expect(screen.getByText("Завершён").parentElement).toHaveTextContent(/2\s000/);
    expect(screen.getByText("Ошибка").parentElement).toHaveTextContent(/500/);
    expect(screen.getAllByText("Ошибка")).toHaveLength(1);
  });
});
