"use client";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { paymentStage } from "@/lib/constants";
import type { Payment } from "@/lib/types";
import { currencySymbol, formatMoney } from "@/lib/utils";
import { QrCodeImage } from "./qr-code-image";
import { TransactionActions, transactionActions, type TransactionActionHandlers } from "./transaction-actions";
import { TransactionDetail } from "./transaction-detail";
import type { Transactions } from "./use-transactions";

function PaymentQrPreview({ payment, onClose }: { payment: Payment; onClose: () => void }) {
  const provider = payment.provider;
  if (!provider) return null;
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`PAY-${String(payment.id).padStart(6, "0")}`}
      title="Kaspi QR готов"
      description="Покажите QR клиенту или откройте оплату на его устройстве. Статус обновится автоматически."
      footer={<Button onClick={onClose}>Готово</Button>}
    >
      <div className="space-y-4 text-center">
        <QrCodeImage provider={provider} />
        {provider.qr_token_url && (
          <Button className="w-full" onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener")}>
            <ExternalLink className="size-4" /> Открыть Kaspi
          </Button>
        )}
      </div>
    </Modal>
  );
}

const pad = (id: number | string) => String(id).padStart(6, "0");

/**
 * Возврат, статус, отклонение, восстановление, QR. `sheet` — окно статуса
 * шторкой (телефон); `detailActions` — в нём же список действий по операции.
 */
export function TransactionModals({
  t,
  canConfirm,
  canCreate,
  sheet = false,
  detailActions = false,
}: {
  t: Transactions;
  canConfirm: boolean;
  canCreate: boolean;
  sheet?: boolean;
  detailActions?: boolean;
}) {
  // Из шторки деталей действие сначала закрывает её, затем открывает свою модалку.
  const handlers: TransactionActionHandlers = {
    busy: t.busy,
    receipt: t.receipt,
    issue: (payment) => {
      t.closeStatus();
      return t.issue(payment);
    },
    openRefund: (payment) => {
      t.closeStatus();
      t.openRefund(payment);
    },
    openReject: (payment) => {
      t.closeStatus();
      t.openReject(payment);
    },
    openRestore: (payment) => {
      t.closeStatus();
      t.openRestore(payment);
    },
  };
  const actions =
    detailActions && t.statusFor ? transactionActions(t.statusFor, handlers, { canConfirm, canCreate }) : [];

  return (
    <>
      <Modal
        open={!!t.refundFor}
        onClose={() => !t.busy && t.setRefundFor(null)}
        eyebrow={t.refundFor?.provider ? "ApiPay · Возврат" : "Касса · Возврат"}
        title="Вернуть оплату"
        description={
          t.refundFor?.provider
            ? "Возврат будет отправлен через ApiPay. Деньги учтутся после подтверждения платёжного сервиса."
            : "Возврат будет сразу проведён как выдача денег из кассы и уменьшит оплаченную сумму заказа."
        }
        footer={
          <>
            <Button variant="outline" onClick={() => t.setRefundFor(null)}>
              Отмена
            </Button>
            <Button disabled={t.busy || !t.amount || !t.reason.trim()} onClick={() => void t.refund()}>
              {t.busy ? "Отправка…" : "Оформить возврат"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {t.error && <p className="text-sm text-[var(--destructive)]">{t.error}</p>}
          <div>
            <label className="mb-1.5 block text-sm">Сумма возврата</label>
            <Input
              type="number"
              min="0.01"
              step="0.01"
              value={t.amount}
              onChange={(e) => t.setAmount(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина</label>
            <Input
              maxLength={500}
              placeholder="Например: возврат товара"
              value={t.reason}
              onChange={(e) => t.setReason(e.target.value)}
            />
          </div>
        </div>
      </Modal>

      <Modal
        open={!!t.statusFor}
        onClose={t.closeStatus}
        variant={sheet ? "sheet" : "dialog"}
        eyebrow="Статус операции"
        title={t.statusFor ? paymentStage(t.statusFor.effective_status ?? t.statusFor.status).label : "Статус"}
        description="Статус показывает, учитываются ли деньги в кассе и что можно сделать с операцией."
        footer={<Button onClick={t.closeStatus}>Понятно</Button>}
      >
        {t.statusFor && (
          <div className="space-y-4">
            <TransactionDetail payment={t.statusFor} />
            {actions.length > 0 && (
              <div>
                <div className="mb-2 text-sm font-medium">Действия</div>
                <TransactionActions actions={actions} layout="list" />
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={!!t.rejectFor}
        onClose={() => !t.busy && t.setRejectFor(null)}
        eyebrow="Касса · Контроль операции"
        title={`Отклонить PAY-${pad(t.rejectFor?.id ?? "")}?`}
        description="Платёж не будет учтён. Для телефонного счёта сначала будет запрошена отмена счёта на оплату."
        footer={
          <>
            <Button variant="outline" disabled={t.busy} onClick={() => t.setRejectFor(null)}>
              Не отклонять
            </Button>
            <Button variant="destructive" disabled={t.busy || !t.rejectReason.trim()} onClick={() => void t.reject()}>
              {t.busy ? "Отклонение…" : "Отклонить платёж"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {t.error && <p className="text-sm text-[var(--destructive)]">{t.error}</p>}
          <div className="rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3 text-sm">
            <div className="font-medium">{t.rejectFor?.client_name}</div>
            <div className="mt-1 text-[var(--muted-foreground)]">
              Заказ #{t.rejectFor?.order} · {formatMoney(t.rejectFor?.amount ?? 0)}{" "}
              {currencySymbol(t.rejectFor?.currency)}
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина отклонения</label>
            <Input
              autoFocus
              maxLength={500}
              placeholder="Например: ошибочно внесённая оплата"
              value={t.rejectReason}
              onChange={(event) => t.setRejectReason(event.target.value)}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!t.restoreFor}
        onClose={() => {
          if (!t.busy) {
            t.setRestoreFor(null);
            t.setError("");
          }
        }}
        title={`Восстановить PAY-${pad(t.restoreFor?.id ?? "")}?`}
        description={
          t.restoreFor?.method === "invoice"
            ? "Операция снова зарезервирует сумму заказа, после чего новый счёт будет отправлен клиенту."
            : "Операция вернётся в очередь кассира. Восстановление доступно только в пределах свободного остатка заказа."
        }
        confirmLabel="Восстановить"
        confirmVariant="default"
        busy={t.busy}
        error={t.error}
        onConfirm={() => void t.restore()}
      />

      {t.qrFor && <PaymentQrPreview payment={t.qrFor} onClose={() => t.setQrFor(null)} />}
    </>
  );
}
