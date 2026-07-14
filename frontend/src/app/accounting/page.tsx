"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { ErrorAlert } from "@/components/ui/data-state";
import { Tabs } from "@/components/ui/tabs";
import { PaymentStageBadge } from "@/components/payment-chain";
import {
  PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE,
} from "@/lib/constants";
import { can, deptLabel } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { CheckCheck, ClipboardCheck, Send, Wallet, ArrowUpRight, RefreshCw, Search } from "lucide-react";
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

/* ── Вкладка «Оплаты»: подтверждение заказов и оплат ────────────────────── */
function PaymentsTab() {
  const router = useRouter();
  const { me } = useAuth();
  const { data: orders, error: loadError, reload: reloadOrders } = useApi<Order[]>("/orders/");
  const { data: queue, reload: reloadQueue } =
    useApi<PaymentQueueItem[]>("/orders/payments-queue/?stage=received");
  const [dept, setDept] = useState("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const list = (orders ?? []).filter((o) => dept === "all" || o.department === dept);
  const pendingOrders = list.filter((o) => o.status === "pending");
  const toReview = (queue ?? []).filter((p) => dept === "all" || p.department === dept);
  const reviewSum = toReview.reduce((s, p) => s + Number(p.amount), 0);
  // Отправка из кассы — только для заявок Отдела 2 (Отдел 1 идёт через пост отгрузки).
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
    <>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Заявок на подтверждение" value={String(pendingOrders.length)} icon={ClipboardCheck} />
        <StatCard label="Оплат к подтверждению" value={String(toReview.length)} icon={CheckCheck} />
        <StatCard label="Сумма к подтверждению" value={`${formatMoney(reviewSum)} ₸`} accent />
      </section>

      <div className="mb-4">
        <FilterDropdown label="Отдел" options={pills} active={dept} onChange={setDept} />
      </div>

      {error && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
          {error}
        </p>
      )}
      {loadError && !orders && <div className="mb-4"><ErrorAlert message={loadError} onRetry={reloadOrders} /></div>}

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
          <CardHeader><CardTitle>Оплаты к подтверждению</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {toReview.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет оплат, ожидающих подтверждения.</p>
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
                    Подтвердить оплату
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
    </>
  );
}

/* ── Вкладка «Долги»: общий долг по клиентам ────────────────────────────── */
interface ClientDebt {
  client_id: number;
  client_name: string;
  client_phone: string;
  debt_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}

function debtPaymentState(row: ClientDebt) {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: "warning" as const };
  }
  if (row.partial_count > 0) {
    return { label: "Частично оплачен", tone: "warning" as const };
  }
  return { label: "Не оплачен", tone: "destructive" as const };
}

function DebtsTab() {
  const { data, loading, error, reload } = useApi<ClientDebt[]>("/clients/debts/");
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const rows = data ?? [];
  const totalDebt = rows.reduce((sum, row) => sum + Number(row.debt_total), 0);
  const totalOrders = rows.reduce((sum, row) => sum + row.orders_count, 0);
  const partialClients = rows.filter((row) => row.partial_count > 0).length;
  const overdueClients = rows.filter((row) => row.overdue_count > 0).length;

  const filtered = rows.filter((row) =>
    !q || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(q.toLowerCase())
  );

  async function checkOverdue() {
    setBusy(true); setCheckMsg("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setCheckMsg(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      await reload();
    } catch (e) {
      setCheckMsg(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue} aria-label="Проверить просрочки">
          <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
          <span className="hidden sm:inline">Проверить просрочки</span>
        </Button>
      </div>

      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <StatCard label="Клиентов с долгом" value={String(rows.length)} />
        <StatCard label="Общий остаток" value={`${formatMoney(String(totalDebt))} ₸`} accent />
        <StatCard label="Заказов в долге" value={String(totalOrders)} />
        <StatCard label="Частично оплачено" value={String(partialClients)} caption={`Просрочек: ${overdueClients}`} />
      </section>

      {checkMsg && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {checkMsg}
        </p>
      )}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Клиенты</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            Общий остаток по клиенту. Заказы открываются внутри клиента.
          </p>
        </div>
        <div className="relative w-full sm:max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту или телефону"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Клиент</TH>
                <TH>Остаток</TH>
                <TH>Заказы</TH>
                <TH>Статус оплаты</TH>
                <TH>Магазины</TH>
                <TH>Просрочки</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
              ) : error && !data ? (
                <TR><TD colSpan={7} className="py-4"><ErrorAlert message={error} onRetry={reload} /></TD></TR>
              ) : filtered.length === 0 ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Долгов нет.</TD></TR>
              ) : filtered.map((row) => {
                const state = debtPaymentState(row);
                return (
                  <TR key={row.client_id}>
                    <TD>
                      <div className="font-medium">{row.client_name || "—"}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                    </TD>
                    <TD className="tabular-nums text-lg font-semibold text-[var(--destructive)]">
                      {formatMoney(row.debt_total)} ₸
                    </TD>
                    <TD className="tabular-nums">{row.orders_count}</TD>
                    <TD>
                      <Badge tone={state.tone} dot>{state.label}</Badge>
                    </TD>
                    <TD>
                      {row.stores_count > 0 ? (
                        <Badge tone="muted">{row.stores_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">—</span>
                      )}
                    </TD>
                    <TD>
                      {row.overdue_count > 0 ? (
                        <Badge tone="destructive" dot>{row.overdue_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">0</span>
                      )}
                    </TD>
                    <TD>
                      <div className="flex justify-end">
                        <Link href={`/accounting/debts/clients/${row.client_id}`}>
                          <Button size="sm" variant="ghost">
                            Детали
                            <ArrowUpRight className="size-4" />
                          </Button>
                        </Link>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}

function CashierInner() {
  const { me } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const canPayments = can(me, "payments.confirm");
  const canDebts = can(me, "reports.view");

  const tabs = [
    ...(canPayments ? [{ key: "payments", label: "Оплаты", icon: CheckCheck }] : []),
    ...(canDebts ? [{ key: "debts", label: "Долги", icon: Wallet }] : []),
  ];
  const requested = params.get("tab");
  const active = tabs.some((t) => t.key === requested) ? requested! : tabs[0]?.key ?? "payments";

  const changeTab = (key: string) => {
    router.replace(`/accounting?tab=${key}`, { scroll: false });
  };

  return (
    <AppShell title="Касса" section="Работа"
      description="Подтверждение заказов и оплат по обоим отделам, контроль долгов клиентов."
      actions={tabs.length > 1
        ? <Tabs tabs={tabs} active={active} onChange={changeTab} />
        : undefined}>
      {active === "debts" ? <DebtsTab /> : <PaymentsTab />}
    </AppShell>
  );
}

export default function CashierPage() {
  // Доступ, если есть хотя бы одна из вкладок: оплаты или долги.
  return (
    <RequirePerm perm={["payments.confirm", "reports.view"]} title="Касса">
      <CashierInner />
    </RequirePerm>
  );
}
