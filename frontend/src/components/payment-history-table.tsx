"use client";
import { useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency as money, formatDateTime } from "@/lib/utils";
import { PAYMENT_STAGE_TONE, PAYMENT_STAGE_LABELS, PAYMENT_METHOD_LABELS } from "@/lib/constants";
/** Платёж из /clients/{id}/history/ — вся история, включая погашенные заказы. */
export interface HistoryPayment {
  can_reopen?: boolean;
  can_reject?: boolean;
  provider?: boolean;
  refunded_amount?: string;
  id: number;
  order_id: number;
  date: string;
  employee: string | null;
  method: string;
  status: string;
  amount: string;
  currency: string;
}
export function PaymentHistoryTable({
  rows,
  emptyText,
  canViewOrders,
  canManagePayments,
  onChanged,
}: {
  rows: HistoryPayment[];
  emptyText: string;
  canViewOrders: boolean;
  canManagePayments: boolean;
  onChanged: () => void;
}) {
  const [target, setTarget] = useState<HistoryPayment | null>(null);
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
              ? `Заказ #${target.order_id} · ${money(target.amount, target.currency)}. ${target.can_reopen ? "Сумма перестанет учитываться как оплаченная, долг увеличится. Платёж вернётся на подтверждение и останется в истории." : "Запись останется в истории. Для онлайн-счёта будет запрошена отмена у платёжного сервиса."}`
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
                  {canViewOrders ? (
                    <Link href={`/orders/${p.order_id}`} className="font-medium hover:underline">
                      #{p.order_id}
                    </Link>
                  ) : (
                    <span className="font-medium">#{p.order_id}</span>
                  )}
                </TD>
                <TD>{PAYMENT_METHOD_LABELS[p.method] ?? p.method}</TD>
                <TD>
                  <Badge tone={PAYMENT_STAGE_TONE[p.status] ?? "muted"}>
                    {PAYMENT_STAGE_LABELS[p.status] ?? p.status}
                  </Badge>
                </TD>
                <TD className="text-[var(--muted-foreground)]">{p.employee ?? "—"}</TD>
                <TD
                  className={cn(
                    "text-right tabular-nums font-semibold",
                    p.status === "confirmed" && "text-[var(--success)]",
                  )}
                >
                  {money(p.amount, p.currency)}
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
