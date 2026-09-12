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
