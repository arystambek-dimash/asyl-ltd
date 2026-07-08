"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { PaymentStageBadge } from "@/components/payment-chain";
import {
  PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE,
} from "@/lib/constants";
import { deptLabel } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { CheckCheck, ClipboardCheck, Send } from "lucide-react";
import type { Order, PaymentQueueItem } from "@/lib/types";

function DepartmentBadge({ department }: { department?: string }) {
  const { me } = useAuth();
  if (!department) return null;
  return (
    <Badge tone={department === "field" ? "primary" : "muted"}>
      {deptLabel(me, department)}
    </Badge>
  );
}

function AccountingInner() {
  const router = useRouter();
  const { me } = useAuth();
  const { data: orders, reload: reloadOrders } = useApi<Order[]>("/orders/");
  const { data: queue, reload: reloadQueue } =
    useApi<PaymentQueueItem[]>("/orders/payments-queue/?stage=received");
  const [dept, setDept] = useState("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const list = (orders ?? []).filter((o) => dept === "all" || o.department === dept);
  const pendingOrders = list.filter((o) => o.status === "pending");
  const toReview = (queue ?? []).filter((p) => dept === "all" || p.department === dept);
  const reviewSum = toReview.reduce((s, p) => s + Number(p.amount), 0);
  // Отправка со стола бухгалтера — только для заявок Отдела 2 (Отдел 1 идёт через пост отгрузки).
  const toShip = list.filter((o) => o.department === "field"
    && ["confirmed", "arrived", "loading", "loaded"].includes(o.status));

  const pills = [
    { key: "all", label: "Все", count: (orders ?? []).length },
    { key: "main", label: deptLabel(me, "main"), count: (orders ?? []).filter((o) => o.department === "main").length },
    { key: "field", label: deptLabel(me, "field"), count: (orders ?? []).filter((o) => o.department === "field").length },
  ];

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reloadOrders(); reloadQueue(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  const confirmOrder = (o: Order) =>
    act(() => api.post(`/orders/${o.id}/confirm/`, {}));
  const shipOrder = (o: Order) =>
    act(() => api.post(`/orders/${o.id}/set-status/`, { status: "shipped" }));
  const confirmPayment = (p: PaymentQueueItem) =>
    act(() => api.post(`/orders/${p.order}/payments/${p.id}/confirm/`));
  const rejectPayment = (p: PaymentQueueItem) =>
    act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`));

  return (
    <AppShell title="Табло бухгалтера" section="Работа"
      description="Подтверждение заказов, отправка и контроль получения оплаты по обоим отделам.">
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Заявок на подтверждение" value={String(pendingOrders.length)} icon={ClipboardCheck} />
        <StatCard label="Оплат на сверку" value={String(toReview.length)} icon={CheckCheck} />
        <StatCard label="Сумма на сверке" value={`${formatMoney(reviewSum)} ₸`} accent />
      </section>

      <div className="mb-4">
        <FilterDropdown label="Отдел" options={pills} active={dept} onChange={setDept} />
      </div>

      {error && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Заявки на подтверждение</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {pendingOrders.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет заявок, ожидающих подтверждения.</p>
            )}
            {pendingOrders.map((o) => {
              const priced = o.items.every((it) => it.unit_price != null);
              return (
                <div key={o.id} className="flex flex-col gap-2 rounded-lg border p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <Link href={`/orders/${o.id}`} className="text-sm font-semibold hover:underline">
                        Заказ #{o.id}
                      </Link>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {o.client_name} · {formatMoney(o.total_amount)} ₸
                      </div>
                    </div>
                    <DepartmentBadge department={o.department} />
                  </div>
                  {priced ? (
                    <Button size="sm" disabled={busy} onClick={() => confirmOrder(o)}>
                      Подтвердить заказ
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline"
                      onClick={() => router.push(`/orders/${o.id}`)}>
                      Указать цены и подтвердить
                    </Button>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Оплаты на сверку</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {toReview.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет оплат, ожидающих сверки.</p>
            )}
            {toReview.map((p) => (
              <div key={p.id} className="flex flex-col gap-2 rounded-lg border p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-base font-semibold tabular-nums">{formatMoney(p.amount)} ₸</div>
                    <div className="text-xs text-[var(--muted-foreground)]">
                      <Link href={`/orders/${p.order}`} className="hover:underline">Заказ #{p.order}</Link>
                      {" · "}{p.client_name} · {p.method_label}
                      {p.received_by_name ? ` · принял ${p.received_by_name}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <DepartmentBadge department={p.department} />
                    <PaymentStageBadge status={p.status} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button size="sm" disabled={busy} onClick={() => confirmPayment(p)}>
                    Сверено — в кассу
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => rejectPayment(p)}>
                    Отклонить
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader><CardTitle>Контроль заказов и оплат</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>№</TH><TH>Отдел</TH><TH>Клиент</TH>
                <TH className="text-right">Сумма</TH>
                <TH className="text-right">Оплачено</TH>
                <TH>Статус</TH><TH>Оплата</TH><TH></TH>
              </TR>
            </THead>
            <TBody>
              {list.map((o) => (
                <TR key={o.id} className="cursor-pointer"
                  onClick={() => router.push(`/orders/${o.id}`)}>
                  <TD className="font-medium">#{o.id}</TD>
                  <TD><DepartmentBadge department={o.department} /></TD>
                  <TD>{o.client_name}</TD>
                  <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                  <TD className="text-right tabular-nums text-[var(--muted-foreground)]">
                    {formatMoney(o.paid_total)} ₸
                  </TD>
                  <TD><StatusBadge status={o.status} dot /></TD>
                  <TD>
                    {o.payment_status && (
                      <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                        {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                      </Badge>
                    )}
                  </TD>
                  <TD onClick={(e) => e.stopPropagation()}>
                    {toShip.some((x) => x.id === o.id) && (
                      <Button size="sm" variant="outline" disabled={busy}
                        onClick={() => shipOrder(o)} title="Отметить отправленным">
                        <Send className="size-3.5" /> Отправлен
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
              {list.length === 0 && (
                <TR><TD colSpan={8} className="py-4 text-center text-[var(--muted-foreground)]">
                  Заказов нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function AccountingPage() {
  return <RequirePerm perm="payments.confirm" title="Табло бухгалтера"><AccountingInner /></RequirePerm>;
}
