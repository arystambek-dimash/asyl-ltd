import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { makePayment } from "@/test-utils/factories";
import { PaymentRefundModal } from "./payment-refund-modal";

const postMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ data: { method: "cash" } });
});

describe("PaymentRefundModal", () => {
  it("refunds the available amount of a payment with a reason", async () => {
    const user = userEvent.setup();
    const onRefunded = vi.fn();
    const cash = makePayment();
    render(<PaymentRefundModal payment={cash} onClose={vi.fn()} onRefunded={onRefunded} />);

    expect(screen.getByRole("dialog", { name: "Вернуть оплату" })).toBeInTheDocument();
    expect(screen.getByLabelText("Сумма возврата")).toHaveValue(100000);
    expect(screen.getByRole("button", { name: "Оформить возврат" })).toBeDisabled();
    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Оформить возврат" }));

    expect(postMock).toHaveBeenCalledWith("/payment-transactions/5/refund/", {
      amount: "100000.00",
      reason: "Переплата",
    });
    await waitFor(() => expect(onRefunded).toHaveBeenCalledWith(cash, null));
  });

  it("lets the cashier pick which payment of the order to refund", async () => {
    const user = userEvent.setup();
    const older = makePayment({ id: 5, available_for_refund: "30000.00" });
    const newer = makePayment({ id: 6, method: "kaspi", available_for_refund: "80000.00" });
    render(
      <PaymentRefundModal
        payment={newer}
        choices={[newer, older]}
        // Переплата 50 000: больше не предлагаем, даже если оплата крупнее.
        amountFor={(p) => String(Math.min(50000, Number(p.available_for_refund)))}
        onClose={vi.fn()}
        onRefunded={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Сумма возврата")).toHaveValue(50000);
    await user.selectOptions(screen.getByLabelText("Оплата"), "5");
    expect(screen.getByLabelText("Сумма возврата")).toHaveValue(30000);
    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Оформить возврат" }));
    expect(postMock).toHaveBeenCalledWith(
      "/payment-transactions/5/refund/",
      expect.objectContaining({ amount: "30000" }),
    );
  });

  it("keeps a failed refund inside the dialog", async () => {
    postMock.mockRejectedValueOnce(new Error("Касса закрыта"));
    const user = userEvent.setup();
    const onRefunded = vi.fn();
    render(<PaymentRefundModal payment={makePayment()} onClose={vi.fn()} onRefunded={onRefunded} />);

    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Оформить возврат" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Касса закрыта");
    expect(onRefunded).not.toHaveBeenCalled();
  });

  it("hands a Kaspi QR refund link over to the caller", async () => {
    const qr = { status: "awaiting_customer" };
    postMock.mockResolvedValueOnce({ data: { method: "apipay_qr", qr_refund: qr } });
    const user = userEvent.setup();
    const onRefunded = vi.fn();
    const online = makePayment({ provider: { channel: "qr" } as Payment["provider"] });
    render(<PaymentRefundModal payment={online} onClose={vi.fn()} onRefunded={onRefunded} />);

    await user.type(screen.getByLabelText("Причина"), "Переплата");
    await user.click(screen.getByRole("button", { name: "Показать QR" }));
    await waitFor(() => expect(onRefunded).toHaveBeenCalledWith(online, qr));
  });
});
