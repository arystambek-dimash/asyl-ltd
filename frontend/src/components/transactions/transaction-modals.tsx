"use client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { paymentStage } from "@/lib/constants";
import type { Payment } from "@/lib/types";
import { formatCurrency, formatPaymentNumber } from "@/lib/utils";
import { PaymentRefundModal } from "./payment-refund-modal";
import { KaspiQr } from "./qr-code-image";
import { TransactionActions, transactionActions } from "./transaction-actions";
import { TransactionDetail } from "./transaction-detail";
import type { TransactionDialogKind, Transactions } from "./use-transactions";

function PaymentQrPreview({ payment, onClose }: { payment: Payment; onClose: () => void }) {
  const provider = payment.provider;
  if (!provider) return null;
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={formatPaymentNumber(payment.id)}
      title="Kaspi QR готов"
      description="Покажите QR клиенту или откройте оплату на его устройстве. Статус обновится автоматически."
      footer={<Button onClick={onClose}>Готово</Button>}
    >
      <div className="space-y-4 text-center">
        <KaspiQr provider={provider} />
      </div>
    </Modal>
  );
}

/**
 * Возврат, статус, отклонение, восстановление, QR. `mobile` — телефон: окно
 * статуса шторкой, в нём же действия по операции с этими правами.
 */
export function TransactionModals({
  t,
  mobile,
}: {
  t: Transactions;
  mobile?: { canConfirm: boolean; canCreate: boolean };
}) {
  const shown = (kind: TransactionDialogKind) => (t.dialog?.kind === kind ? t.dialog.payment : null);
  const statusFor = shown("status");
  const refundFor = shown("refund");
  const rejectFor = shown("reject");
  const restoreFor = shown("restore");
  const reopenFor = shown("reopen");
  const qrFor = shown("qr");
  // Закрытие окна сбрасывает его ошибку, чтобы она не всплыла на странице.
  const dismiss = () => {
    if (t.busy) return;
    t.close();
    t.setError("");
  };
  // Действие из шторки открывает своё окно вместо неё.
  const actions = mobile && statusFor ? transactionActions(statusFor, t, mobile) : [];

  return (
    <>
      {refundFor && (
        <PaymentRefundModal key={refundFor.id} payment={refundFor} onClose={t.close} onRefunded={t.refunded} />
      )}

      <Modal
        open={!!statusFor}
        onClose={t.close}
        variant={mobile ? "sheet" : "dialog"}
        eyebrow="Статус операции"
        title={statusFor ? paymentStage(statusFor).label : "Статус"}
        description="Статус показывает, учитываются ли деньги в кассе и что можно сделать с операцией."
        footer={<Button onClick={t.close}>Понятно</Button>}
      >
        {statusFor && (
          <div className="space-y-4">
            <TransactionDetail payment={statusFor} />
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
        open={!!rejectFor}
        onClose={dismiss}
        eyebrow="Касса · Контроль операции"
        title={`Отклонить ${formatPaymentNumber(rejectFor?.id ?? "")}?`}
        description="Платёж не будет учтён."
        footer={
          <>
            <Button variant="outline" disabled={t.busy} onClick={dismiss}>
              Не отклонять
            </Button>
            <Button
              variant="destructive"
              disabled={t.busy || !t.rejectReason.trim()}
              onClick={() => rejectFor && void t.reject(rejectFor)}
            >
              {t.busy ? "Отклонение…" : "Отклонить платёж"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {t.error && <p className="text-sm text-[var(--destructive)]">{t.error}</p>}
          <div className="rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3 text-sm">
            <div className="font-medium">{rejectFor?.client_name}</div>
            <div className="mt-1 text-[var(--muted-foreground)]">
              Заказ #{rejectFor?.order} · {formatCurrency(rejectFor?.amount ?? 0, rejectFor?.currency)}
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
        open={!!restoreFor}
        onClose={dismiss}
        title={`Восстановить ${formatPaymentNumber(restoreFor?.id ?? "")}?`}
        description={
          restoreFor?.method === "invoice"
            ? "Операция снова зарезервирует сумму заказа, после чего новый счёт будет отправлен клиенту."
            : "Операция вернётся в очередь кассира. Восстановление доступно только в пределах свободного остатка заказа."
        }
        confirmLabel="Восстановить"
        confirmVariant="default"
        busy={t.busy}
        error={t.error}
        onConfirm={() => restoreFor && void t.restore(restoreFor)}
      />

      <ConfirmDialog
        open={!!reopenFor}
        onClose={dismiss}
        title={`Вернуть ${formatPaymentNumber(reopenFor?.id ?? "")} на проверку?`}
        description="Подтверждение отменится: сумма уйдёт из поступлений, а оплата снова появится в «Оплаты → Проверка»."
        confirmLabel="Вернуть на проверку"
        confirmVariant="default"
        busy={t.busy}
        error={t.error}
        onConfirm={() => reopenFor && void t.reopen(reopenFor)}
      />

      {qrFor && <PaymentQrPreview payment={qrFor} onClose={t.close} />}
      {t.qrRefund.modal}
    </>
  );
}
