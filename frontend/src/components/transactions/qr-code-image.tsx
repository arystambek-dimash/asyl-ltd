"use client";
import Image from "next/image";
import { useState } from "react";
import { QrCode } from "lucide-react";
import type { Payment } from "@/lib/types";

/** Kaspi QR платёжного сервиса; без картинки — подсказка открыть оплату кнопкой. */
export function QrCodeImage({ provider }: { provider: NonNullable<Payment["provider"]> }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  if (provider.qr_image_url && failedUrl !== provider.qr_image_url) {
    return (
      <Image
        src={provider.qr_image_url}
        alt="Kaspi QR для оплаты"
        width={288}
        height={288}
        unoptimized
        onError={() => setFailedUrl(provider.qr_image_url)}
        className="mx-auto size-72 max-w-full rounded-2xl bg-white p-3 shadow-sm"
      />
    );
  }
  return (
    <div className="mx-auto flex aspect-square w-72 max-w-full flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--muted)]/35 p-6">
      <QrCode className="size-12 text-[var(--muted-foreground)]" />
      <p className="mt-3 text-sm text-[var(--muted-foreground)]">
        Изображение QR недоступно. Откройте оплату кнопкой ниже.
      </p>
    </div>
  );
}
