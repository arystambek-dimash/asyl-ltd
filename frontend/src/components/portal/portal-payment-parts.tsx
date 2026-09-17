"use client";
import { useState } from "react";
import Image from "next/image";
import { ArrowLeft, Clock, FileText, QrCode, Smartphone } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { PortalOrder } from "@/lib/types";
import { currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";

export type PortalPaymentPart = PortalOrder["payment_parts"][number];

export const PAYABLE_PROVIDER_STATUSES = new Set(["creating", "processing", "pending"]);

const PROVIDER_STATUS_LABELS: Record<string, string> = {
  creating: "Создаётся",
  processing: "Ожидает оплаты",
  pending: "Ожидает оплаты",
  paid: "Оплачен",
  cancelling: "Отменяется",
  cancelled: "Отменён",
  expired: "Истёк",
  superseded: "Заменён",
  error: "Ошибка",
};

function providerStatusTone(status: string): "muted" | "success" | "warning" | "destructive" {
  if (status === "paid") return "success";
  if (PAYABLE_PROVIDER_STATUSES.has(status) || status === "cancelling") return "warning";
  if (status === "error") return "destructive";
  return "muted";
}

/** Начатые оплаты заказа: живой Kaspi QR крупно, счёт на телефон — статусом. */
export function PortalPaymentParts({
  order,
  busy,
  onRelease,
}: {
  order: PortalOrder;
  busy: boolean;
  onRelease: (part: PortalPaymentPart) => void;
}) {
  const [failedQrImages, setFailedQrImages] = useState<Set<number>>(() => new Set());
  const symbol = currencySymbol(order.currency);

  return (
    <>
      {order.payment_parts.map((part) => {
        const provider = part.apipay_invoice;
        const providerIsPayable = provider != null && PAYABLE_PROVIDER_STATUSES.has(provider.status);
        const qrIsPayable = part.status !== "confirmed" && provider?.channel === "qr" && providerIsPayable;
        const qrImageFailed = failedQrImages.has(part.id);
        const paymentState =
          part.status === "confirmed"
            ? "Оплата подтверждена"
            : provider?.status === "paid"
              ? "Оплата получена, обновляем статус заказа"
              : provider?.status === "cancelling"
                ? "Отмена отправлена, ожидаем подтверждение"
                : part.method === "cash"
                  ? "Ожидает подтверждения кассиром"
                  : providerIsPayable
                    ? "Подтвердится автоматически после оплаты"
                    : "Этот платёж больше не активен";

        return (
          <div key={part.id} className="rounded-2xl border border-[var(--border)] bg-[var(--muted)]/25 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[var(--card)] shadow-sm">
                  {part.method === "kaspi" ? (
                    <QrCode className="size-4 text-[var(--primary)]" />
                  ) : part.method === "invoice" ? (
                    <FileText className="size-4 text-[var(--primary)]" />
                  ) : (
                    <Clock className="size-4 text-[var(--warning)]" />
                  )}
                </span>
                <div className="min-w-0">
                  <div className="font-semibold tabular-nums">
                    {formatMoney(part.amount)} {symbol}
                  </div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {part.method === "kaspi" ? "Kaspi QR" : part.method === "invoice" ? "Счёт в Kaspi" : "Наличными"}
                    {" · "}
                    {paymentState}
                  </div>
                </div>
              </div>
              {part.can_release && provider?.status !== "cancelling" && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRelease(part)}>
                  <ArrowLeft className="size-4" />{" "}
                  {order.payment_status === "settled" ? "Закрыть лишнюю" : "Другой способ"}
                </Button>
              )}
            </div>

            {provider && (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3 text-xs text-[var(--muted-foreground)]">
                <Badge tone={providerStatusTone(provider.status)} dot>
                  {PROVIDER_STATUS_LABELS[provider.status] ?? provider.status}
                </Badge>
                {provider.channel === "phone" && provider.phone_number && (
                  <span>Счёт отправлен на {provider.phone_number}</span>
                )}
                {provider.channel === "qr" && provider.qr_expires_at && providerIsPayable && (
                  <span>QR действует до {formatDateTime(provider.qr_expires_at)}</span>
                )}
              </div>
            )}

            {qrIsPayable && (
              <div className="mt-4 flex flex-col items-center gap-3">
                {provider.qr_image_url && !qrImageFailed ? (
                  <Image
                    src={provider.qr_image_url}
                    alt="Kaspi QR для оплаты"
                    width={240}
                    height={240}
                    unoptimized
                    onError={() => setFailedQrImages((current) => new Set(current).add(part.id))}
                    className="size-60 max-w-full rounded-2xl bg-white p-2 shadow-sm"
                  />
                ) : (
                  <div className="flex size-60 max-w-full flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-[var(--border)] bg-white p-6 text-center">
                    <QrCode className="size-12 text-[var(--primary)]" />
                    <p className="text-sm text-[var(--muted-foreground)]">
                      {provider.qr_token_url
                        ? "QR готов. Откройте Kaspi кнопкой ниже."
                        : "QR создаётся. Обновите страницу через несколько секунд."}
                    </p>
                  </div>
                )}
                {provider.qr_token_url && (
                  <Button
                    className="h-12 w-full text-base"
                    onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener,noreferrer")}
                  >
                    <Smartphone className="size-4" /> Открыть Kaspi
                  </Button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
