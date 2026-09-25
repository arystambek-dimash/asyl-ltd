"use client";
import { useState } from "react";
import Image from "next/image";
import { ArrowLeft, Clock, FileText, QrCode, Smartphone, Wallet, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { invoiceIsActive, invoiceIsPayable, invoiceStatusLabel } from "@/lib/apipay-invoice";
import type { BadgeTone } from "@/lib/constants";
import type { PortalOrder } from "@/lib/types";
import { formatCurrency, formatDateTime } from "@/lib/utils";

export type PortalPaymentPart = PortalOrder["payment_parts"][number];

function providerStatusTone(status: string): BadgeTone {
  if (status === "paid") return "success";
  if (status === "partially_refunded") return "primary";
  if (invoiceIsActive(status)) return "warning";
  if (status === "error") return "destructive";
  return "muted";
}

/** Способ оплаты словами терминала кабинета: так же подписаны плитки «Kaspi QR» и «Счёт в Kaspi». */
const PART_METHODS: Record<PortalPaymentPart["method"], { label: string; icon: LucideIcon; iconClass: string }> = {
  kaspi: { label: "Kaspi QR", icon: QrCode, iconClass: "text-[var(--primary)]" },
  invoice: { label: "Счёт в Kaspi", icon: FileText, iconClass: "text-[var(--primary)]" },
  // Удалённую оплату сотрудник отмечает уже полученной — к наличным она отношения не имеет.
  remote: { label: "Удалённая оплата", icon: Wallet, iconClass: "text-[var(--primary)]" },
  cash: { label: "Наличными", icon: Clock, iconClass: "text-[var(--warning)]" },
};

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

  return (
    <>
      {order.payment_parts.map((part) => {
        const provider = part.apipay_invoice;
        const providerIsPayable = provider != null && invoiceIsPayable(provider.status);
        const qrIsPayable = part.status !== "confirmed" && provider?.channel === "qr" && providerIsPayable;
        const qrImageFailed = failedQrImages.has(part.id);
        // Легаси-способы (card) в кабинете не выбираются, но старые оплаты могут их нести.
        const method = PART_METHODS[part.method] ?? {
          label: part.method_label,
          icon: FileText,
          iconClass: "text-[var(--muted-foreground)]",
        };
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
                  <method.icon className={`size-4 ${method.iconClass}`} />
                </span>
                <div className="min-w-0">
                  <div className="font-semibold tabular-nums">{formatCurrency(part.amount, order.currency)}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {method.label}
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
                  {invoiceStatusLabel(provider.status)}
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
