/* eslint-disable @next/next/no-img-element */
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { QrCodeImage } from "./qr-code-image";

vi.mock("next/image", () => ({
  default: ({ src, alt, onError }: { src: string; alt: string; onError?: () => void }) => (
    <img src={src} alt={alt} onError={onError} />
  ),
}));

function provider(url: string | null): NonNullable<Payment["provider"]> {
  return {
    invoice_id: 1,
    channel: "qr",
    status: "pending",
    phone_number: null,
    qr_token_url: null,
    qr_image_url: url,
    qr_expires_at: null,
    total_refunded: "0.00",
    available_for_refund: "0.00",
    refunds: [],
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
