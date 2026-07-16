"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Banknote, CheckCircle2, Clock, FileText, HandCoins, QrCode } from "lucide-react";
import { useApi } from "@/lib/use-api";
import { apiError } from "@/lib/api";
import { formatMoney, formatPortalMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { clientStep, payOrder, setTruck, getPaymentInfo, type PaymentInfo } from "@/lib/portal-actions";
import type { PortalOrder, PortalPaymentMethod } from "@/lib/types";

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, reload } = useApi<PortalOrder>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [info, setInfo] = useState<PaymentInfo | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reload(); } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function selectPayment(method: PortalPaymentMethod) {
    if (method === "kaspi") setInfo(await getPaymentInfo());
    else setInfo(null);
    await payOrder(Number(id), method);
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
                {formatPortalMoney(order.total_amount)}
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

        {step === "pay" && (() => {
          const total = Number(order.total_amount ?? 0);
          const paid = Number(order.paid_total ?? 0);
          const pct = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;
          const partial = paid > 0 && remaining > 0;
          return (
          <Card>
            <CardHeader><CardTitle>Оплата</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-4">
              {/* сводка */}
              <div className="grid grid-cols-3 gap-2 text-sm">
                <div><div className="text-xs text-[var(--muted-foreground)]">Сумма</div>
                  <div className="tabular-nums font-medium">{formatMoney(order.total_amount ?? 0)} ₸</div></div>
                <div><div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
                  <div className="tabular-nums text-[var(--success)]">{formatMoney(order.paid_total ?? "0")} ₸</div></div>
                <div><div className="text-xs text-[var(--muted-foreground)]">Остаток</div>
                  <div className="tabular-nums font-semibold text-[var(--destructive)]">{formatMoney(remaining)} ₸</div></div>
              </div>
              <div>
                <div className="mb-1.5 flex justify-between text-xs text-[var(--muted-foreground)]">
                  <span>{partial ? "Частично оплачено" : "Прогресс оплаты"}</span>
                  <span className="tabular-nums">{pct}%</span>
                </div>
                <ProgressBar pct={pct} />
              </div>

              {order.debt_requested ? (
                <div className="flex items-start gap-2 rounded-lg border border-[var(--primary)]/25 bg-[var(--primary)]/5 p-3 text-sm">
                  <HandCoins className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
                  Запрос «В долг» отправлен. Ожидайте решения сотрудника.
                </div>
              ) : order.has_pending_payment ? (
                <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 p-3 text-sm text-[var(--warning)]">
                  <Clock className="mt-0.5 size-4 shrink-0" />
                  Заявка на оплату отправлена. Ожидает подтверждения сотрудником.
                </div>
              ) : (
                <>
                  <p className="text-sm text-[var(--muted-foreground)]">
                    Заказ отгружен. Выберите удобный способ оплаты.
                  </p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <Button disabled={busy} variant="outline" className="h-auto justify-start py-3"
                      onClick={() => run(() => selectPayment("invoice"))}>
                      <FileText className="size-4" /> Счет на оплату
                    </Button>
                    <Button disabled={busy} variant="outline" className="h-auto justify-start py-3"
                      onClick={() => run(() => selectPayment("kaspi"))}>
                      <QrCode className="size-4" /> Каспи
                    </Button>
                    <Button disabled={busy} variant="outline" className="h-auto justify-start py-3"
                      onClick={() => run(() => selectPayment("cash"))}>
                      <Banknote className="size-4" /> Наличными
                    </Button>
                    <Button disabled={busy} variant="outline" className="h-auto justify-start py-3"
                      onClick={() => run(() => selectPayment("debt"))}>
                      <HandCoins className="size-4" /> В долг
                    </Button>
                  </div>
                </>
              )}

              {info && (
                <div className="rounded-lg border p-3 text-sm">
                  <p>{info.instructions}</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {info.kaspi_qr && <img src={info.kaspi_qr} alt="Kaspi QR" className="mt-2 size-40" />}
                  {info.account && <p className="mt-1">Счёт: {info.account}</p>}
                </div>
              )}
              <p className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                <CheckCircle2 className="size-3.5" /> Выбранный способ зафиксируется в заказе.
              </p>
            </CardContent>
          </Card>
          );
        })()}

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
