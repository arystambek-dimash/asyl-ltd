import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OrderPaymentBadge, paymentBadgeStatus } from "./order-payment-badge";

describe("paymentBadgeStatus", () => {
  it("always shows the payment state of a shipped order", () => {
    expect(paymentBadgeStatus({ status: "shipped", payment_status: "unpaid" })).toBe("unpaid");
    expect(paymentBadgeStatus({ status: "shipped", payment_status: "settled" })).toBe("settled");
  });

  it("marks a prepaid order before shipment, but not an unpaid one: it is not a debt yet", () => {
    expect(paymentBadgeStatus({ status: "confirmed", payment_status: "settled" })).toBe("settled");
    expect(paymentBadgeStatus({ status: "loading", payment_status: "partial" })).toBe("partial");
    expect(paymentBadgeStatus({ status: "confirmed", payment_status: "unpaid" })).toBeNull();
    expect(paymentBadgeStatus({ status: "pending", payment_status: "unpaid" })).toBeNull();
    expect(paymentBadgeStatus({ status: "shipped" })).toBeNull();
  });
});

describe("OrderPaymentBadge", () => {
  it("renders the label of a prepaid order and nothing for an unpaid one", () => {
    const { rerender, container } = render(
      <OrderPaymentBadge order={{ status: "arrived", payment_status: "settled" }} />,
    );
    expect(screen.getByText("Оплачен")).toBeInTheDocument();
    rerender(<OrderPaymentBadge order={{ status: "arrived", payment_status: "unpaid" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows «На проверке» over the payment status while a payment waits for the till", () => {
    render(<OrderPaymentBadge order={{ status: "shipped", payment_status: "unpaid" }} pending />);
    expect(screen.getByText("На проверке")).toBeInTheDocument();
    expect(screen.queryByText("Не оплачен")).not.toBeInTheDocument();
  });
});
