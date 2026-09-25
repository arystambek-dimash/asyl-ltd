import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Order, Payment } from "@/lib/types";
import { makeMe, makePayment } from "@/test-utils/factories";
import { OverpaymentRefundButton, refundablePayments } from "./overpayment-refund";

const postMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { post: postMock }, apiError: () => "Ошибка" }));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const cashier = makeMe({ permissions: ["payments.confirm"] });

const order = {
  id: 40,
  currency: "KZT",
  status: "confirmed",
  total_amount: "70000",
  paid_total: "100000",
  overpaid_amount: "30000.00",
  payments: [makePayment({ id: 1, order: 40 })],
} as unknown as Order;

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ data: { method: "cash" } });
});

describe("refundablePayments", () => {
  it("offers confirmed money that can still be returned, newest first", () => {
    const rows = refundablePayments({
      ...order,
      payments: [
        makePayment({ id: 1, order: 40, paid_at: "2026-09-01T10:00:00Z" }),
        makePayment({ id: 2, order: 40, paid_at: "2026-09-10T10:00:00Z" }),
        makePayment({ id: 3, order: 40, available_for_refund: "0.00" }),
      ],
    } as Order);
    expect(rows.map((row) => row.id)).toEqual([2, 1]);
  });
});

describe("OverpaymentRefundButton", () => {
  it("returns only the overpaid part and refreshes the order", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OverpaymentRefundButton order={order} me={cashier} onChanged={onChanged} onQrRefund={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Вернуть переплату/ }));
    expect(screen.getByLabelText("Сумма возврата")).toHaveValue(30000);
    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Оформить возврат" }));

    expect(postMock).toHaveBeenCalledWith("/payment-transactions/1/refund/", {
      amount: "30000",
      reason: "Переплата",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.queryByRole("dialog", { name: "Вернуть оплату" })).not.toBeInTheDocument();
  });

  it("hands a Kaspi QR refund over to the page that keeps its window", async () => {
    const user = userEvent.setup();
    const qr = { status: "awaiting_customer" };
    postMock.mockResolvedValueOnce({ data: { method: "apipay_qr", qr_refund: qr } });
    const online = makePayment({ id: 1, order: 40, provider: { channel: "qr" } as Payment["provider"] });
    const onChanged = vi.fn();
    const onQrRefund = vi.fn();
    render(
      <OverpaymentRefundButton
        order={{ ...order, payments: [online] } as Order}
        me={cashier}
        onChanged={onChanged}
        onQrRefund={onQrRefund}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Вернуть переплату/ }));
    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Показать QR" }));

    await waitFor(() => expect(onQrRefund).toHaveBeenCalledWith(online, qr));
    expect(onChanged).toHaveBeenCalled();
  });

  it("is hidden without overpayment and explains who can refund it", () => {
    const { container, rerender } = render(
      <OverpaymentRefundButton
        order={{ ...order, overpaid_amount: "0.00" } as Order}
        me={cashier}
        onChanged={vi.fn()}
        onQrRefund={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(
      <OverpaymentRefundButton
        order={order}
        me={{ ...cashier, permissions: ["payments.create"] }}
        onChanged={vi.fn()}
        onQrRefund={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /Вернуть переплату/ })).not.toBeInTheDocument();
    expect(screen.getByText("Вернуть может сотрудник с правом подтверждения оплат.")).toBeInTheDocument();
  });
});
