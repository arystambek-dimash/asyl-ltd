import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PortalOrder } from "@/lib/types";
import { PortalPaymentParts, type PortalPaymentPart } from "./portal-payment-parts";

function part(overrides: Partial<PortalPaymentPart> = {}): PortalPaymentPart {
  return {
    id: 1,
    amount: "20000.00",
    method: "kaspi",
    method_label: "QR",
    status: "confirmed",
    can_release: false,
    apipay_invoice: null,
    ...overrides,
  };
}

function renderParts(parts: PortalPaymentPart[]) {
  const order = { id: 642, currency: "KZT", payment_status: "partial", payment_parts: parts } as PortalOrder;
  render(<PortalPaymentParts order={order} busy={false} onRelease={vi.fn()} />);
}

describe("начатые оплаты в кабинете клиента", () => {
  it("подписывает удалённую оплату её способом, а не «Наличными»", () => {
    renderParts([part({ method: "remote" })]);
    expect(screen.getByText(/Удалённая оплата · Оплата подтверждена/)).toBeInTheDocument();
    expect(screen.queryByText(/Наличными/)).not.toBeInTheDocument();
  });

  it("подписывает способы Kaspi и наличные как на терминале", () => {
    renderParts([
      part({ id: 1, method: "kaspi" }),
      part({ id: 2, method: "invoice" }),
      part({ id: 3, method: "cash", status: "received" }),
    ]);
    expect(screen.getByText(/Kaspi QR · Оплата подтверждена/)).toBeInTheDocument();
    expect(screen.getByText(/Счёт в Kaspi · Оплата подтверждена/)).toBeInTheDocument();
    expect(screen.getByText(/Наличными · Ожидает подтверждения кассиром/)).toBeInTheDocument();
  });

  it("не падает на легаси-способе старой оплаты", () => {
    renderParts([part({ method: "card" as PortalPaymentPart["method"], method_label: "Карта" })]);
    expect(screen.getByText(/Карта · Оплата подтверждена/)).toBeInTheDocument();
  });

  it("показывает частичный возврат по счёту словами, а не кодом провайдера", () => {
    renderParts([
      part({
        apipay_invoice: {
          invoice_id: 77,
          status: "partially_refunded",
          channel: "qr",
          phone_number: null,
          qr_token_url: null,
          qr_image_url: null,
          qr_expires_at: null,
        },
      }),
    ]);
    expect(screen.getByText("Частично возвращён")).toBeInTheDocument();
    expect(screen.queryByText("partially_refunded")).not.toBeInTheDocument();
  });
});
