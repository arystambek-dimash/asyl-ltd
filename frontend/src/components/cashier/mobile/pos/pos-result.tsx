"use client";
import { CircleCheck, CircleX, ExternalLink } from "lucide-react";
import { QrCodeImage } from "@/components/transactions/qr-code-image";
import { Button } from "@/components/ui/button";
import type { Payment } from "@/lib/types";
import { formatCurrency, formatTime } from "@/lib/utils";
import type { PaymentOutcome } from "./pos-logic";

/** QR на экране, ожидание, «Оплачено» или «больше не действует». */
export function PosResult({
  payment,
  outcome,
  clientName,
  error,
  onRetry,
  onNew,
}: {
  payment: Payment;
  outcome: PaymentOutcome;
  clientName: string;
  error?: string;
  onRetry: () => void;
  onNew: () => void;
}) {
  const provider = payment.provider;
  const qr = provider?.channel === "qr";
  const amount = formatCurrency(payment.amount, payment.currency ?? "KZT");
  const caption = `${clientName} · заказ #${payment.order}`;

  if (outcome === "paid") {
    return (
      <section className="flex flex-col items-center gap-3 py-8 text-center">
        <CircleCheck className="size-16 text-[var(--success)]" aria-hidden />
        <h2 className="text-2xl font-bold">Оплачено</h2>
        <div className="text-[28px] font-bold tabular-nums">{amount}</div>
        <p className="text-sm text-[var(--muted-foreground)]">{caption}</p>
        <Button className="mt-4 h-12 w-full text-base" onClick={onNew}>
          Новая оплата
        </Button>
      </section>
    );
  }

  if (outcome === "failed") {
    return (
      <section className="flex flex-col items-center gap-3 py-8 text-center">
        <CircleX className="size-16 text-[var(--destructive)]" aria-hidden />
        <h2 className="text-xl font-bold">{qr ? "QR больше не действует" : "Счёт больше не действует"}</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          {caption} · {amount}
        </p>
        {qr && provider?.status === "error" && (
          <p className="text-xs text-[var(--muted-foreground)]">
            Сервис вернул ошибку — старый QR ещё может пройти. Прежде чем брать оплату иначе, проверьте «Историю».
          </p>
        )}
        <Button className="mt-4 h-12 w-full text-base" onClick={onRetry}>
          {qr ? "Создать новый QR" : "Отправить заново"}
        </Button>
        <Button variant="outline" className="h-11 w-full" onClick={onNew}>
          Новая оплата
        </Button>
      </section>
    );
  }

  return (
    <section className="flex flex-col items-center gap-4 text-center">
      <div>
        <div className="text-[32px] font-bold tabular-nums">{amount}</div>
        <p className="text-sm text-[var(--muted-foreground)]">{caption}</p>
      </div>
      {qr && provider ? (
        <>
          <QrCodeImage key={provider.qr_image_url ?? "no-image"} provider={provider} />
          {provider.qr_token_url && (
            <Button
              variant="outline"
              className="h-11 w-full"
              onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener")}
            >
              <ExternalLink className="size-4" /> Открыть Kaspi
            </Button>
          )}
          {provider.qr_expires_at && (
            <p className="text-xs text-[var(--muted-foreground)]">
              QR действует до {formatTime(provider.qr_expires_at)}
            </p>
          )}
        </>
      ) : (
        <p className="text-sm">
          Счёт отправлен{provider?.phone_number ? ` на ${provider.phone_number}` : ""}. Клиент оплатит в Kaspi.
        </p>
      )}
      <p role="status" className="flex items-center gap-2 text-sm font-medium text-[var(--muted-foreground)]">
        <span className="size-2 rounded-full bg-[var(--warning)]" aria-hidden />
        Ожидаем оплату…
      </p>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          Не удаётся проверить статус: {error}. Проверяем снова…
        </p>
      )}
      {qr && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Не создавайте второй QR на эту сумму — старый ещё можно оплатить.
        </p>
      )}
      <Button variant="outline" className="h-11 w-full" onClick={onNew}>
        {qr ? "Новая оплата" : "Готово"}
      </Button>
    </section>
  );
}
