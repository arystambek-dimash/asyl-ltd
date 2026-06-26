"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { clientStep, payOrder, setTruck, getPaymentInfo, type PaymentInfo } from "@/lib/portal-actions";
import type { Order } from "@/lib/types";

function portalMoney(value: string | null | undefined) {
  if (value == null) return "После подтверждения";
  return `${formatMoney(value)} ₸`;
}

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, reload } = useApi<Order>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [info, setInfo] = useState<PaymentInfo | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reload(); } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  if (loading || !order) return (
    <AppShell title="Заказ" portal>
      <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
    </AppShell>
  );

  const step = clientStep(order.status, order.payment_status);
  const remaining = order.remaining_amount == null
    ? 0
    : Number(order.remaining_amount);

  return (
    <AppShell title={`Заказ #${order.id}`} portal>
      <div className="flex flex-col gap-4 max-w-2xl">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Заказ #{order.id}</CardTitle>
            <div className="flex items-center gap-2">
              <StatusBadge status={order.status} />
              {order.status === "shipped" && order.payment_status && (
                <Badge tone={PAYMENT_STATUS_TONE[order.payment_status] ?? "muted"} dot>
                  {PAYMENT_STATUS_LABELS[order.payment_status] ?? order.payment_status}
                </Badge>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Товар</TH><TH>Мешков</TH></TR></THead>
              <TBody>{order.items.map((it) => (
                <TR key={it.id}><TD>{it.product_label}</TD><TD>{it.quantity}</TD></TR>
              ))}</TBody>
            </Table>
            <div className="mt-4 flex justify-between border-t pt-3 text-sm">
              <span className="text-[var(--muted-foreground)]">Итого</span>
              <span className={order.total_amount == null
                ? "font-medium text-[var(--muted-foreground)]"
                : "font-bold tabular-nums"}>
                {portalMoney(order.total_amount)}
              </span>
            </div>
          </CardContent>
        </Card>

        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}

        {step === "pending" && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
            Заказ на рассмотрении. Ожидайте решения.</CardContent></Card>
        )}

        {step === "rejected" && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--destructive)]">
            Заказ отклонён.</CardContent></Card>
        )}

        {step === "pay" && (
          <Card>
            <CardHeader><CardTitle>Оплата · к оплате {formatMoney(remaining)} ₸</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-[var(--muted-foreground)]">
                Заказ отгружен. До оплаты сумма числится как долг.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button disabled={busy} variant="outline" onClick={() => run(() => payOrder(order.id, "card"))}>Оплатил картой</Button>
                <Button disabled={busy} variant="outline"
                  onClick={() => run(async () => { setInfo(await getPaymentInfo()); await payOrder(order.id, "kaspi"); })}>
                  Оплатить Kaspi QR</Button>
              </div>
              {info && (
                <div className="rounded-md border p-3 text-sm">
                  <p>{info.instructions}</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {info.kaspi_qr && <img src={info.kaspi_qr} alt="Kaspi QR" className="mt-2 size-40" />}
                  {info.account && <p className="mt-1">Счёт: {info.account}</p>}
                </div>
              )}
              <p className="text-xs text-[var(--muted-foreground)]">Оплата картой/Kaspi подтверждается сотрудником.</p>
            </CardContent>
          </Card>
        )}

        {step === "truck" && (
          <Card>
            <CardHeader><CardTitle>Отправка КАМАЗа</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-[var(--muted-foreground)]">
                Укажите номер КАМАЗа. Оплата станет доступна после отгрузки.
              </p>
              {order.truck_number && <p className="text-sm">Текущий номер: <b>{order.truck_number}</b></p>}
              <div className="flex gap-2">
                <Input placeholder="Номер КАМАЗа" value={truck} onChange={(e) => setTruckVal(e.target.value)} />
                <Button disabled={busy || !truck} onClick={() => run(() => setTruck(order.id, truck))}>Сохранить</Button>
              </div>
            </CardContent>
          </Card>
        )}

        {(step === "shipping" || step === "done") && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
            {order.truck_number && <p className="mb-1">КАМАЗ: <b>{order.truck_number}</b></p>}
            {step === "done" ? "Заказ отгружен." : "Заказ в обработке на складе."}</CardContent></Card>
        )}
      </div>
    </AppShell>
  );
}
