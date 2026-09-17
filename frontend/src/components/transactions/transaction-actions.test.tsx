import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { TransactionActions, transactionActions, type TransactionActionHandlers } from "./transaction-actions";

const confirmed: Payment = {
  id: 5,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  status: "confirmed",
  paid_at: "2026-09-12T10:00:00",
  recorded_by: null,
  available_for_refund: "100",
};

function handlers(): TransactionActionHandlers {
  return {
    busy: false,
    receipt: vi.fn(),
    issue: vi.fn(),
    openRefund: vi.fn(),
    openReject: vi.fn(),
    openRestore: vi.fn(),
  };
}

it("offers receipt and refund for a confirmed payment when the cashier may confirm", async () => {
  const h = handlers();
  const actions = transactionActions(confirmed, h, { canConfirm: true, canCreate: false });
  expect(actions.map((action) => action.key)).toEqual(["receipt", "refund"]);
  render(<TransactionActions actions={actions} layout="list" />);
  await userEvent.click(screen.getByRole("button", { name: /Вернуть оплату/ }));
  expect(h.openRefund).toHaveBeenCalledWith(confirmed);
  expect(screen.getByRole("button", { name: /Скачать выписку/ })).toBeInTheDocument();
});

it("hides money actions without the confirm permission and keeps icon titles", () => {
  const actions = transactionActions(confirmed, handlers(), { canConfirm: false, canCreate: false });
  expect(actions.map((action) => action.key)).toEqual(["receipt"]);
  render(<TransactionActions actions={actions} layout="icons" />);
  expect(screen.getByRole("button", { name: "Скачать выписку ASYL LTD" })).toBeInTheDocument();
});

it("lets the cashier reject a manual payment that is still open", () => {
  const actions = transactionActions({ ...confirmed, status: "received" }, handlers(), {
    canConfirm: true,
    canCreate: true,
  });
  expect(actions.map((action) => action.key)).toEqual(["reject"]);
});

it("shows the buyer-link refund state for a Kaspi QR payment with a pending link refund", async () => {
  const h = { ...handlers(), openQrRefund: vi.fn() };
  const qrPayment: Payment = {
    ...confirmed,
    method: "kaspi",
    pending_refund_amount: "100",
    available_for_refund: "0",
    refunds: [
      {
        id: 1,
        amount: "100",
        method: "apipay_qr",
        status: "pending",
        reason: "Ошибочная оплата",
        requested_by_name: null,
        completed_at: null,
        created_at: "2026-09-17T10:00:00",
      },
    ],
  };

  const actions = transactionActions(qrPayment, h, { canConfirm: true, canCreate: false });

  expect(actions.map((action) => action.key)).toEqual(["receipt", "qr_refund"]);
  render(<TransactionActions actions={actions} layout="list" />);
  await userEvent.click(screen.getByRole("button", { name: /Возврат по ссылке/ }));
  expect(h.openQrRefund).toHaveBeenCalledWith(qrPayment);
});
