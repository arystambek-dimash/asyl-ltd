"use client";
import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { OrderRef } from "@/components/orders/order-ref";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, formatDateTime } from "@/lib/utils";
import { PaymentStageBadge } from "@/components/payment-chain";
import type { ClientHistoryPayment } from "@/lib/types";
export function PaymentHistoryTable({
  rows,
  emptyText,
  canViewOrders,
  canManagePayments,
  onChanged,
}: {
  rows: ClientHistoryPayment[];
  emptyText: string;
  canViewOrders: boolean;
  canManagePayments: boolean;
  onChanged: () => void;
}) {
  const [target, setTarget] = useState<ClientHistoryPayment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function cancelPayment() {
    if (!target || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.post(
        `/orders/${target.order_id}/payments/${target.id}/${target.can_reopen ? "reopen" : "reject"}/`,
      );
      setNotice(
        response.status === 202
          ? "Запрос отмены отправлен платёжному сервису. Ожидаем подтверждения."
          : target.can_reopen
            ? "Подтверждение отменено. Оплата возвращена в очередь, долг пересчитан."
            : "Оплата отклонена.",
      );
      setTarget(null);
      onChanged();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }
  if (rows.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">{emptyText}</CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="pt-5">
        {notice && (
          <p role="status" className="mb-3 text-sm">
            {notice}
          </p>
        )}
        <ConfirmDialog
          open={!!target}
          onClose={() => {
            if (!busy) setTarget(null);
          }}
          title={target?.can_reopen ? "Отменить подтверждение оплаты?" : "Отклонить оплату?"}
          description={
            target
              ? `Заказ #${target.order_id} · ${formatCurrency(target.amount, target.currency)}. ${target.can_reopen ? "Сумма перестанет учитываться как оплаченная, долг увеличится. Платёж вернётся на подтверждение и останется в истории." : "Запись останется в истории. Для онлайн-счёта будет запрошена отмена у платёжного сервиса."}`
              : ""
          }
          confirmLabel={target?.can_reopen ? "Отменить подтверждение" : "Отклонить оплату"}
          busy={busy}
          error={error}
          onConfirm={() => void cancelPayment()}
        />
        <Table>
          <THead>
            <TR>
              <TH>Дата</TH>
              <TH>Заказ</TH>
              <TH>Способ</TH>
              <TH>Статус</TH>
              <TH>Сотрудник</TH>
              <TH className="text-right">Сумма</TH>
              {canManagePayments && <TH className="text-right">Действия</TH>}
            </TR>
          </THead>
          <TBody>
            {rows.map((p) => (
              <TR key={p.id}>
                <TD className="tabular-nums">{formatDateTime(p.date)}</TD>
                <TD>
                  <OrderRef id={p.order_id} canOpen={canViewOrders} className="font-medium">
                    #{p.order_id}
                  </OrderRef>
                </TD>
                <TD>{p.method_label}</TD>
                <TD>
                  <PaymentStageBadge payment={p} dot={false} />
                </TD>
                <TD className="text-[var(--muted-foreground)]">{p.employee ?? "—"}</TD>
                <TD
                  className={cn(
                    "text-right tabular-nums font-semibold",
                    p.status === "confirmed" && "text-[var(--success)]",
                  )}
                >
                  {formatCurrency(p.amount, p.currency)}
                </TD>
                {canManagePayments && (
                  <TD className="text-right">
                    {p.can_reopen || p.can_reject ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setError("");
                          setTarget(p);
                        }}
                      >
                        {p.can_reopen ? "Отменить подтверждение" : "Отклонить"}
                      </Button>
                    ) : p.provider && p.status === "confirmed" ? (
                      <span className="text-xs text-[var(--muted-foreground)]">
                        Онлайн-оплата · нужен возврат в транзакциях
                      </span>
                    ) : (
                      "—"
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
