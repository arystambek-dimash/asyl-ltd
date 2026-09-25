import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { ApiPayInvoiceView } from "@/lib/types";
import { QrCodeImage } from "./qr-code-image";

vi.mock("next/image", () => import("@/test-utils/next-image"));

function provider(url: string | null): ApiPayInvoiceView {
  return {
    invoice_id: 1,
    channel: "qr",
    status: "pending",
    phone_number: null,
    qr_token_url: null,
    qr_image_url: url,
    qr_expires_at: null,
  };
}

it("falls back when the QR image fails and recovers for a new QR", () => {
  const { rerender } = render(<QrCodeImage provider={provider("https://api.apipay.kz/qr/a.png")} />);
  fireEvent.error(screen.getByRole("img", { name: "Kaspi QR для оплаты" }));
  expect(screen.getByText("Изображение QR недоступно. Откройте оплату кнопкой ниже.")).toBeInTheDocument();

  rerender(<QrCodeImage provider={provider("https://api.apipay.kz/qr/b.png")} />);
  expect(screen.getByRole("img", { name: "Kaspi QR для оплаты" })).toHaveAttribute(
    "src",
    "https://api.apipay.kz/qr/b.png",
  );
});

it("shows the fallback when there is no image", () => {
  render(<QrCodeImage provider={provider(null)} />);
  expect(screen.getByText("Изображение QR недоступно. Откройте оплату кнопкой ниже.")).toBeInTheDocument();
});
