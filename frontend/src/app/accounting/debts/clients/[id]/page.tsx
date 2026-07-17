"use client";
import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Tabs } from "@/components/ui/tabs";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate } from "@/components/ui/data-state";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, formatMoney, formatDateTime } from "@/lib/utils";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import {
  PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE,
  CASHIER_PAYMENT_METHOD_LABELS, CASHIER_PAYMENT_METHODS,
  PAYMENT_STAGE_LABELS, PAYMENT_STAGE_TONE, PAYMENT_METHOD_LABELS,
} from "@/lib/constants";
import {
  ArrowLeft, Building2, Calendar, Clock, ExternalLink, FileText,
  Info, ShieldCheck, Truck, Wallet,
} from "lucide-react";
import type { Client, Order } from "@/lib/types";

const money = formatCurrency;

interface DebtStore {
  id: number;
  name: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
  window_open: boolean;
}

interface ClientDebtDetail {
  client: Client;
  debt_total: string;
  lifetime_total: string;
  lifetime_paid: string;
  overdue_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores: DebtStore[];
  orders: Order[];
}

/** Платёж из /clients/{id}/history/ — вся история, включая погашенные заказы. */
interface HistoryPayment {
  id: number; order_id: number; date: string; employee: string | null;
  method: string; status: string; amount: string;
}
interface ClientHistory { payments: HistoryPayment[] }

const WEEKDAYS = ["", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
function scheduleLabel(store: DebtStore) {
  if (store.payment_schedule_type === "none") return "Свободная оплата";
  if (store.payment_schedule_type === "monthly") {
    return store.payment_days.length ? `Числа: ${store.payment_days.join(", ")}` : "Числа не заданы";
  }
  return store.payment_days.length
    ? `Дни: ${store.payment_days.map((d) => WEEKDAYS[d] ?? d).join(", ")}`
    : "Дни не заданы";
}

function remainingOf(order: Order): number {
  return Number(order.remaining_amount ?? (Number(order.total_amount) - Number(order.paid_total)));
}

function pendingSum(order: Order): number {
  return (order.pending_payments ?? []).reduce((s, p) => s + Number(p.amount), 0);
}

/* ── Счёт по заказу: позиции, скидка от прайса, итог ────────────────────── */
function InvoiceTable({ order }: { order: Order }) {
  const lines = order.items.map((it) => {
    const price = Number(it.price ?? it.base_price ?? 0);
    const base = Number(it.base_price ?? it.price ?? 0);
    return {
      key: it.id ?? `${it.product}`,
      label: it.product_label ?? `Товар #${it.product}`,
      qty: it.quantity,
      price,
      discount: Math.max(0, (base - price) * it.quantity),
      total: price * it.quantity,
      listTotal: base * it.quantity,
    };
  });
  const listSum = lines.reduce((s, l) => s + l.listTotal, 0);
  const toPay = Number(order.total_amount);
  const discount = Math.max(0, listSum - toPay);
  return (
    <div>
      <Table>
        <THead>
          <TR>
            <TH>Товар</TH>
            <TH className="text-right">Количество</TH>
            <TH className="text-right">Цена за единицу, ₸</TH>
            <TH className="text-right">Скидка</TH>
            <TH className="text-right">Итого</TH>
          </TR>
        </THead>
        <TBody>
          {lines.map((l) => (
            <TR key={l.key}>
              <TD className="font-medium">{l.label}</TD>
              <TD className="text-right tabular-nums">{l.qty}</TD>
              <TD className="text-right tabular-nums">{money(l.price)}</TD>
              <TD className="text-right tabular-nums">
                {l.discount > 0 ? money(l.discount) : <span className="text-[var(--muted-foreground)]">—</span>}
              </TD>
              <TD className="text-right tabular-nums font-medium">{money(l.total)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <div className="ml-auto mt-3 flex max-w-xs flex-col gap-1.5 border-t pt-3 text-sm">
        <div className="flex justify-between">
          <span className="text-[var(--muted-foreground)]">Сумма</span>
          <span className="tabular-nums">{money(listSum)}</span>
        </div>
        {discount > 0 && (
          <div className="flex justify-between">
            <span className="text-[var(--muted-foreground)]">Скидка</span>
            <span className="tabular-nums text-[var(--destructive)]">−{money(discount)}</span>
          </div>
        )}
        <div className="flex justify-between text-base font-semibold">
          <span>К оплате</span>
          <span className="tabular-nums">{money(toPay)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Списания: погашения по заказу, включая цепочку подтверждения ───────── */
function WriteOffList({ order }: { order: Order }) {
  const rows = [...(order.payments ?? []), ...(order.pending_payments ?? [])];
  if (rows.length === 0) {
    return <p className="py-4 text-sm text-[var(--muted-foreground)]">Списаний пока нет.</p>;
  }
  return (
    <div className="flex flex-col divide-y">
      {rows.map((p) => (
        <div key={p.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="tabular-nums">{formatDateTime(p.paid_at)}</span>
              <span className="text-[var(--muted-foreground)]">{p.method_label ?? p.method}</span>
              <Badge tone={PAYMENT_STAGE_TONE[p.status] ?? "muted"}>
                {PAYMENT_STAGE_LABELS[p.status] ?? p.status}
              </Badge>
            </div>
            {(p.recorded_by_name || p.note) && (
              <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                {[p.recorded_by_name, p.note].filter(Boolean).join(" · ")}
              </div>
            )}
          </div>
          <span className={cn("shrink-0 tabular-nums font-semibold",
            p.status === "confirmed" ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
            +{money(p.amount)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Карточка заказа в долге ────────────────────────────────────────────── */
function OrderDebtCard({ order, selectable, selected, onSelect }: {
  order: Order;
  selectable: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const [tab, setTab] = useState("invoice");
  const status = order.payment_status ?? "unpaid";
  const pct = Math.min(100, Math.round((Number(order.paid_total) / Math.max(1, Number(order.total_amount))) * 100));
  const pending = pendingSum(order);
  return (
    <Card
      onClick={selectable ? onSelect : undefined}
      className={cn(selectable && "cursor-pointer transition-shadow",
        selectable && selected && "ring-2 ring-[var(--foreground)]")}
    >
      <CardContent className="flex flex-col gap-4 pt-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-[var(--destructive)]/10 text-[var(--destructive)]">
              <FileText className="size-5" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-bold tracking-tight">Заказ #{order.id}</span>
                <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot>
                  {PAYMENT_STATUS_LABELS[status] ?? status}
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
                <span className="flex items-center gap-1.5">
                  <Calendar className="size-3.5" /> Создан: {formatDateTime(order.created_at)}
                </span>
                {order.shipped_at && (
                  <span className="flex items-center gap-1.5">
                    <Truck className="size-3.5" /> Отгружен: {formatDateTime(order.shipped_at)}
                  </span>
                )}
                {order.department && (
                  <span className="flex items-center gap-1.5">
                    <Building2 className="size-3.5" /> {order.department_name ?? order.department}
                  </span>
                )}
                {order.truck_number && <span className="tabular-nums">{order.truck_number}</span>}
              </div>
            </div>
          </div>
          <Link href={`/orders/${order.id}`} onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="outline">
              Открыть заказ <ExternalLink className="size-3.5" />
            </Button>
          </Link>
        </div>

        <div className="grid grid-cols-2 gap-4 border-t pt-4 sm:grid-cols-4 sm:divide-x sm:[&>div+div]:pl-4">
          <div>
            <div className="text-xs text-[var(--muted-foreground)]">Сумма заказа</div>
            <div className="mt-0.5 tabular-nums font-semibold">{money(order.total_amount)}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
            <div className="mt-0.5 tabular-nums font-semibold text-[var(--success)]">{money(order.paid_total)}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--muted-foreground)]">Остаток долга</div>
            <div className="mt-0.5 tabular-nums font-semibold text-[var(--destructive)]">{money(remainingOf(order))}</div>
          </div>
          <div>
            <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
              <span>Прогресс оплаты</span>
              <span className="tabular-nums">{pct}%</span>
            </div>
            <ProgressBar pct={pct} className="mt-2.5" />
          </div>
        </div>

        {pending > 0 && (
          <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">
            <span className="flex items-center gap-1.5 text-[var(--warning)]">
              <Info className="size-4" />
              Ожидает подтверждения оплаты бухгалтером (бухгалтер → касса)
            </span>
            <span className="tabular-nums font-semibold text-[var(--warning)]">{money(pending)}</span>
          </div>
        )}

        <div className="border-t pt-1">
          <Tabs
            tabs={[{ key: "invoice", label: "Счёт" }, { key: "writeoff", label: "Списание" }]}
            active={tab}
            onChange={setTab}
          />
          <div className="pt-3">
            {tab === "invoice" ? <InvoiceTable order={order} /> : <WriteOffList order={order} />}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/* ── История платежей / счета (из истории клиента) ──────────────────────── */
function PaymentHistoryTable({ rows, emptyText }: { rows: HistoryPayment[]; emptyText: string }) {
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
        <Table>
          <THead>
            <TR>
              <TH>Дата</TH>
              <TH>Заказ</TH>
              <TH>Способ</TH>
              <TH>Статус</TH>
              <TH>Сотрудник</TH>
              <TH className="text-right">Сумма</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((p) => (
              <TR key={p.id}>
                <TD className="tabular-nums">{formatDateTime(p.date)}</TD>
                <TD>
                  <Link href={`/orders/${p.order_id}`} className="font-medium hover:underline">
                    #{p.order_id}
                  </Link>
                </TD>
                <TD>{PAYMENT_METHOD_LABELS[p.method] ?? p.method}</TD>
                <TD>
                  <Badge tone={PAYMENT_STAGE_TONE[p.status] ?? "muted"}>
                    {PAYMENT_STAGE_LABELS[p.status] ?? p.status}
                  </Badge>
                </TD>
                <TD className="text-[var(--muted-foreground)]">{p.employee ?? "—"}</TD>
                <TD className={cn("text-right tabular-nums font-semibold",
                  p.status === "confirmed" && "text-[var(--success)]")}>
                  {money(p.amount)}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

/* ── Панель «Внести оплату» ─────────────────────────────────────────────── */
const QUICK_FRACTIONS = [
  { label: "25%", f: 0.25 },
  { label: "50%", f: 0.5 },
  { label: "75%", f: 0.75 },
  { label: "Весь долг", f: 1 },
];

function PayPanel({ orders, selectedId, onSelect, blockedFor, onPaid, onError }: {
  orders: Order[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  blockedFor: (order: Order) => DebtStore | null;
  onPaid: (msg: string) => Promise<void>;
  onError: (msg: string) => void;
}) {
  const order = orders.find((o) => o.id === selectedId) ?? orders[0];
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  // Смена заказа — сумма и примечание относятся к прошлому заказу, сбрасываем.
  useEffect(() => { setAmount(""); setNote(""); }, [order?.id]);

  if (!order) return null;
  const remaining = remainingOf(order);
  const blockingStore = blockedFor(order);
  const quickValue = (f: number) => Math.round(remaining * f * 100) / 100;

  async function pay() {
    setBusy(true); onError("");
    try {
      await api.post(`/orders/${order.id}/payments/`, { amount, method, note });
      setAmount(""); setNote("");
      await onPaid(`Оплата по заказу #${order.id} добавлена в очередь. Подтвердите получение в кассе.`);
    } catch (e) { onError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <Card className="xl:sticky xl:top-20">
      <CardContent className="flex flex-col gap-4 pt-5">
        <div className="flex items-center gap-2">
          <Wallet className="size-4" />
          <span className="text-lg font-bold tracking-tight">Внести оплату</span>
        </div>

        {orders.length > 1 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-sm text-[var(--muted-foreground)]">Заказ</span>
            <Select value={String(order.id)} onValueChange={(v) => onSelect(Number(v))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {orders.map((o) => (
                  <SelectItem key={o.id} value={String(o.id)}>
                    Заказ #{o.id} — остаток {money(remainingOf(o))}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div>
          <div className="text-sm text-[var(--muted-foreground)]">Остаток к оплате</div>
          <div className="mt-1 text-[28px] font-bold leading-none tracking-tight tabular-nums text-[var(--destructive)]">
            {money(remaining)}
          </div>
        </div>

        {blockingStore ? (
          <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2.5 text-sm text-[var(--warning)]">
            <Clock className="mt-0.5 size-4 shrink-0" />
            <span>
              Оплата заблокирована: магазин «{blockingStore.name}» платит только по расписанию
              ({scheduleLabel(blockingStore)}).
            </span>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <span className="text-sm text-[var(--muted-foreground)]">Сумма платежа</span>
              <Input type="number" placeholder="0" value={amount}
                onChange={(e) => setAmount(e.target.value)} />
            </div>

            <div className="grid grid-cols-4 gap-2">
              {QUICK_FRACTIONS.map(({ label, f }) => {
                const v = quickValue(f);
                const active = amount !== "" && Number(amount) === v;
                return (
                  <button key={label} type="button" onClick={() => setAmount(String(v))}
                    className={cn(
                      "flex flex-col items-center gap-0.5 rounded-lg border px-1 py-2 transition-colors",
                      active
                        ? "border-[var(--foreground)] bg-[var(--muted)]"
                        : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                    )}>
                    <span className="text-sm font-semibold">{label}</span>
                    <span className="text-[11px] tabular-nums text-[var(--muted-foreground)]">
                      {formatMoney(v)} ₸
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-sm text-[var(--muted-foreground)]">Способ оплаты</span>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CASHIER_PAYMENT_METHODS.map((m) => (
                    <SelectItem key={m} value={m}>{CASHIER_PAYMENT_METHOD_LABELS[m]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-sm text-[var(--muted-foreground)]">Примечание</span>
              <Input placeholder="Введите примечание (необязательно)" value={note}
                onChange={(e) => setNote(e.target.value)} />
            </div>

            <Button className="w-full" disabled={busy || !amount || Number(amount) <= 0}
              onClick={pay}>
              Добавить в очередь
            </Button>
          </>
        )}

        <p className="flex items-start gap-2 border-t pt-3 text-xs text-[var(--muted-foreground)]">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          Платёж уменьшит долг только после ручного подтверждения получения кассиром.
        </p>
      </CardContent>
    </Card>
  );
}

/* ── Страница ───────────────────────────────────────────────────────────── */
function ClientDebtPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const isAccountant = can(me, "payments.create");
  const { data, loading, error: loadError, reload } = useApi<ClientDebtDetail>(`/clients/${id}/debt-detail/`);
  const { data: history, reload: reloadHistory } = useApi<ClientHistory>(`/clients/${id}/history/`);
  const [tab, setTab] = useState("orders");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const payments = useMemo(() => history?.payments ?? [], [history]);
  const invoices = useMemo(() => payments.filter((p) => p.status === "requested"), [payments]);

  if (!data) {
    return (
      <AppShell title="Долг клиента">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );
  }

  const storeById = new Map(data.stores.map((s) => [s.id, s]));
  // Магазин с расписанием блокирует оплату вне окна.
  function blockedFor(order: Order): DebtStore | null {
    if (order.store == null) return null;
    const s = storeById.get(order.store);
    if (!s || s.payment_schedule_type === "none" || s.window_open) return null;
    return s;
  }

  async function onPaid(msg: string) {
    setNotice(msg);
    await reload();
    reloadHistory();
  }

  return (
    <AppShell title={`Долг · ${data.client.name}`} section="Касса"
      description={data.client.phone ? `Телефон: ${data.client.phone}` : undefined}
      actions={
        <Link href="/accounting">
          <Button size="sm" variant="outline">
            <ArrowLeft className="size-4" />
            К долгам
          </Button>
        </Link>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Текущий долг" tone="destructive" caption="к погашению"
          value={money(data.debt_total)} />
        <StatCard label="Общая задолженность" caption="всего за всё время"
          value={money(data.lifetime_total)} />
        <StatCard label="Оплачено" tone="success" caption="всего оплачено"
          value={money(data.lifetime_paid)} />
        <StatCard label="Просрочено" tone="destructive" caption="просроченные суммы"
          value={money(data.overdue_total)} />
      </section>

      {error && <p className="mb-4 text-sm text-[var(--destructive)]">{error}</p>}
      {notice && (
        <p className="mb-4 rounded-lg border border-[var(--success)]/30 bg-[var(--success)]/10 px-3 py-2 text-sm text-[var(--success)]">
          {notice}
        </p>
      )}

      <div className={cn("grid grid-cols-1 items-start gap-5",
        isAccountant && "xl:grid-cols-[minmax(0,1fr)_360px]")}>
        <div className="flex flex-col gap-4">
          <Tabs active={tab} onChange={setTab}
            tabs={[
              { key: "orders", label: "Заказы в долге", count: data.orders.length },
              { key: "history", label: "История платежей", count: payments.length },
              { key: "invoices", label: "Счета", count: invoices.length },
            ]} />

          {tab === "orders" && (
            data.orders.length === 0 ? (
              <Card>
                <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                  Долгов нет.
                </CardContent>
              </Card>
            ) : data.orders.map((order) => (
              <OrderDebtCard key={order.id} order={order}
                selectable={isAccountant && data.orders.length > 1}
                selected={(selectedId ?? data.orders[0]?.id) === order.id}
                onSelect={() => setSelectedId(order.id)} />
            ))
          )}
          {tab === "history" && (
            <PaymentHistoryTable rows={payments} emptyText="Платежей пока нет." />
          )}
          {tab === "invoices" && (
            <PaymentHistoryTable rows={invoices} emptyText="Выставленных счетов нет." />
          )}
        </div>

        {isAccountant && data.orders.length > 0 && (
          <PayPanel orders={data.orders}
            selectedId={selectedId ?? data.orders[0]?.id ?? null}
            onSelect={setSelectedId}
            blockedFor={blockedFor}
            onPaid={onPaid}
            onError={setError} />
        )}
      </div>
    </AppShell>
  );
}

export default function ClientDebtPage(props: { params: Promise<{ id: string }> }) {
  return <RequirePerm perm="reports.view" title="Долг клиента"><ClientDebtPageInner {...props} /></RequirePerm>;
}
