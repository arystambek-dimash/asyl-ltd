"use client";
import { useCallback, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, Send, Share2 } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { copyText, whatsappLink } from "@/lib/clipboard";
import { showSuccess } from "@/lib/toast";
import type { Payment, QrRefundState } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatCurrency, formatDateTime, formatPaymentNumber } from "@/lib/utils";

function stepText(state: QrRefundState): string {
  switch (state.status) {
    case "issuing":
      return "Выпускаем ссылку…";
    case "awaiting_customer":
      return "Покажите QR покупателю или отправьте ему ссылку. Он подтвердит возврат в Kaspi — деньги вернутся автоматически.";
    case "activating":
    case "awaiting_scan":
      return "Покупатель открыл ссылку. Ждём подтверждения в Kaspi…";
    case "customer_identified":
      return state.operations.length > 0
        ? "Покупатель подтвердил. Выберите его оплату, по которой вернуть деньги."
        : "Покупатель подтвердил. Возвращаем деньги…";
    case "executing":
      return "Возвращаем деньги через Kaspi…";
    default:
      return "";
  }
}

/** Возврат по Kaspi QR как в POS-терминале: QR на экране кассира (та же ссылка — поделиться) и живой статус до денег. */
export function QrRefundModal({
  payment,
  initial,
  onClose,
  onChanged,
}: {
  payment: Payment;
  initial: QrRefundState | null;
  onClose: () => void;
  onChanged: () => Promise<unknown>;
}) {
  const url = `/payment-transactions/${payment.id}/qr-refund/`;
  const { data, error: loadError, reload } = useApi<QrRefundState>(url);
  const [override, setOverride] = useState<QrRefundState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Ответ POST возврата — первая точка, дальше опрос; ответ действия — свежее опроса.
  const state = override ?? data ?? initial;
  const waiting = !!state?.active;
  useVisiblePolling(
    async () => {
      setOverride(null);
      await reload();
    },
    3_000,
    waiting && !busy,
  );
  const link = state?.customer_url ?? null;
  const shareText = link ? `Возврат оплаты ASYL LTD: откройте ссылку и подтвердите возврат в Kaspi ${link}` : "";

  async function act(action: "revoke" | "execute", body?: object) {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<QrRefundState>(`${url}${action}/`, body);
      setOverride(response.data);
      await onChanged();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    if (await copyText(link)) showSuccess("Ссылка скопирована");
    else setError("Не удалось скопировать — выделите ссылку вручную.");
  }

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      eyebrow={`${formatPaymentNumber(payment.id)} · Kaspi QR`}
      title="Возврат по QR"
      description={`${payment.client_name ?? "Покупатель"} · ${formatCurrency(state?.amount ?? payment.amount, payment.currency)}`}
      footer={<Button onClick={onClose}>Закрыть</Button>}
    >
      <div className="space-y-4">
        {!state &&
          (loadError ? (
            <p className="text-sm text-[var(--destructive)]">{loadError}</p>
          ) : (
            <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ))}
        {state && waiting && (
          <div className="flex gap-3 rounded-lg bg-[var(--muted)]/60 p-3 text-sm">
            <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-[var(--muted-foreground)]" />
            <p>
              {stepText(state)}
              {state.client_name && (
                <span className="mt-1 block text-[var(--muted-foreground)]">Покупатель: {state.client_name}</span>
              )}
            </p>
          </div>
        )}

        {link && (
          <div className="space-y-3">
            {/* Покупатель у кассы сканирует QR камерой телефона; если его нет рядом — та же ссылка в мессенджер. */}
            <figure className="flex flex-col items-center gap-2">
              <QRCodeSVG
                value={link}
                size={224}
                level="M"
                marginSize={2}
                title="QR для возврата оплаты"
                className="size-56 max-w-full rounded-2xl bg-white shadow-sm"
              />
              <figcaption className="text-center text-xs text-[var(--muted-foreground)]">
                Покупатель сканирует камерой телефона и подтверждает возврат в Kaspi
              </figcaption>
            </figure>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {typeof navigator !== "undefined" && "share" in navigator && (
                <Button
                  variant="outline"
                  onClick={() => void navigator.share({ text: shareText }).catch(() => undefined)}
                >
                  <Share2 className="size-4" /> Поделиться
                </Button>
              )}
              <Button variant="outline" onClick={() => window.open(whatsappLink("", shareText), "_blank", "noopener")}>
                <Send className="size-4" /> WhatsApp
              </Button>
              <Button variant="outline" onClick={() => void copy()}>
                <Copy className="size-4" /> Скопировать
              </Button>
            </div>
            <p className="text-xs text-[var(--muted-foreground)]">
              Отправьте только тому, кто платил: кто откроет ссылку, тот и получит возврат.
              {state?.link_expires_at && ` Действует до ${formatDateTime(state.link_expires_at)}.`}
            </p>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void act("revoke")}>
              Отозвать ссылку
            </Button>
          </div>
        )}

        {state?.status === "customer_identified" && state.operations.length > 0 && (
          <div className="space-y-2">
            {state.operations.map((operation) => (
              <div
                key={operation.ref}
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm"
              >
                <div>
                  <div className="font-medium tabular-nums">{formatCurrency(operation.amount, payment.currency)}</div>
                  {operation.date && (
                    <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(operation.date)}</div>
                  )}
                </div>
                <Button size="sm" disabled={busy} onClick={() => void act("execute", { operation_ref: operation.ref })}>
                  Вернуть
                </Button>
              </div>
            ))}
          </div>
        )}

        {state?.status === "completed" && (
          <div className="flex gap-3 rounded-lg bg-[var(--success)]/10 p-3 text-sm">
            <CheckCircle2 className="size-5 shrink-0 text-[var(--success)]" />
            <div>
              <div className="font-medium">
                Деньги возвращены: {formatCurrency(state.refunded_amount ?? state.amount, payment.currency)}
              </div>
              {state.receipt_url && (
                <a href={state.receipt_url} target="_blank" rel="noreferrer" className="text-xs underline">
                  Чек Kaspi
                </a>
              )}
            </div>
          </div>
        )}

        {state && ["failed", "expired", "execution_uncertain"].includes(state.status) && (
          <div className="flex gap-3 rounded-lg bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]">
            <AlertTriangle className="size-5 shrink-0" />
            <p>{state.error_message || "Возврат не выполнен. Выпустите новую ссылку."}</p>
          </div>
        )}
        {state?.error_message && waiting && state.status !== "customer_identified" && (
          <p className="text-xs text-[var(--muted-foreground)]">{state.error_message}</p>
        )}
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}

/**
 * Окно возврата по Kaspi QR, начатого кнопкой в строке или баннере. Начатый
 * возврат сразу резервирует деньги: переплата исчезает, и строка «К возврату»
 * или баннер карточки уходят после перезагрузки — вместе с ними ушло бы и окно
 * со ссылкой. Поэтому окно держит родитель, который перезагрузку переживает.
 */
export function useQrRefundWindow(onChanged: () => unknown) {
  // initial=null — окно уже начатого возврата: состояние подтянет опрос.
  const [started, setStarted] = useState<{ payment: Payment; initial: QrRefundState | null } | null>(null);
  const start = useCallback((payment: Payment, initial: QrRefundState | null) => setStarted({ payment, initial }), []);
  const modal = started && (
    <QrRefundModal
      key={started.payment.id}
      payment={started.payment}
      initial={started.initial}
      onClose={() => setStarted(null)}
      onChanged={async () => onChanged()}
    />
  );
  return { start, modal };
}
