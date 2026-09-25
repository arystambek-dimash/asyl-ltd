import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { TransactionModals } from "./transaction-modals";
import type { Transactions } from "./use-transactions";

const payment = { id: 7, order: 3, currency: "KZT", amount: "5000.00", status: "confirmed" } as Payment;

describe("TransactionModals", () => {
  it("clears the reject error when the reject dialog is dismissed", async () => {
    const user = userEvent.setup();
    const t = {
      busy: false,
      error: "Счёт уже оплачен",
      dialog: { kind: "reject", payment },
      rejectReason: "",
      close: vi.fn(),
      setRejectReason: vi.fn(),
      setError: vi.fn(),
      qrRefund: { modal: null },
    } as unknown as Transactions;
    render(<TransactionModals t={t} />);

    expect(screen.getByText("Счёт уже оплачен")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Не отклонять" }));

    expect(t.close).toHaveBeenCalled();
    expect(t.setError).toHaveBeenCalledWith("");
  });
});
