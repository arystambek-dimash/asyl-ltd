"use client";
import { Fragment, use, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Tabs } from "@/components/ui/tabs";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError, blobApiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { cn, formatCompactCurrency, formatCurrency, formatDateTime } from "@/lib/utils";
import { can } from "@/lib/can";
import { PaidMethodBreakdown } from "@/components/payment-chain";
import { useAuth } from "@/store/auth";
import {
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONE,
  CASHIER_PAYMENT_METHODS,
  PAYMENT_STAGE_LABELS,
  PAYMENT_STAGE_TONE,
  PAYMENT_METHOD_LABELS,
} from "@/lib/constants";
import { ArrowLeft, ChevronDown, Clock, ExternalLink, Info, Plus, ShieldCheck, Trash2, Wallet } from "lucide-react";
import type { Client, Order, Payment } from "@/lib/types";
import { formatPaymentSchedule } from "@/app/stores/schedule-validation";

const money = formatCurrency;
const compactMoney = formatCompactCurrency;

function CurrencyRows({ totals, primary }: { totals: Record<string, string | number>; primary: string }) {
  const rows = otherCurrencyAmounts(totals, primary);
  if (rows.length === 0) return null;
  return (
    <div className="grid gap-0.5 text-xs text-[var(--muted-foreground)]">
      {rows.map(([currency, amount]) => (
        <span key={currency}>Также {money(amount, currency)}</span>
      ))}
    </div>
  );
}

interface DebtStore {
  id: number;
  name: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
  window_open: boolean;
}

interface ClientDebtDetail {
  client: Client;
  /** Суммы в основной валюте клиента — её же показывают плитки сверху. */
  debt_total: string;
  debt_currency?: "KZT" | "USD";
  debt_by_currency?: Record<string, string>;
  lifetime_total: string;
  lifetime_paid: string;
  lifetime_by_currency?: Record<string, { total: string; paid: string }>;
  overdue_total: string;
  overdue_by_currency?: Record<string, string>;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores: DebtStore[];
  orders: Order[];
}

/** Платёж из /clients/{id}/history/ — вся история, включая погашенные заказы. */
interface HistoryPayment {
  id: number;
  order_id: number;
  date: string;
  employee: string | null;
  method: string;
  status: string;
  amount: string;
  currency: string;
}
interface ClientHistory {
  payments: HistoryPayment[];
}

function remainingOf(order: Order): number {
  return Number(order.remaining_amount ?? Number(order.total_amount) - Number(order.paid_total));
}

function pendingSum(order: Order): number {
  return (order.pending_payments ?? []).reduce((s, p) => s + Number(p.amount), 0);
}

function moneyCents(value: number | string): number {
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.round(amount * 100) : 0;
}

/* ── Счёт по заказу: зафиксированные клиентские цены ───────────────────── */
function InvoiceTable({ order }: { order: Order }) {
  const lines = order.items.map((it) => {
    const price = Number(it.price ?? 0);
    return {
      key: it.id ?? `${it.product}`,
      label: it.product_label ?? `Товар #${it.product}`,
      qty: it.quantity,
      price,
      total: price * it.quantity,
    };
  });
  const total = Number(order.total_amount);
  const paid = Number(order.paid_total);
  const toPay = remainingOf(order);
  return (
    <div>
      <Table>
        <THead>
          <TR>
            <TH>Товар</TH>
            <TH className="text-right">Количество</TH>
            <TH className="text-right">Цена за единицу</TH>
            <TH className="text-right">Итого</TH>
          </TR>
        </THead>
        <TBody>
          {lines.map((l) => (
            <TR key={l.key}>
              <TD className="font-medium">{l.label}</TD>
              <TD className="text-right tabular-nums">{l.qty}</TD>
              <TD className="text-right tabular-nums">{money(l.price, order.currency)}</TD>
              <TD className="text-right tabular-nums font-medium">{money(l.total, order.currency)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <div className="ml-auto mt-3 flex max-w-xs flex-col gap-1.5 border-t pt-3 text-sm">
        <div className="flex justify-between text-[var(--muted-foreground)]">
          <span>Сумма заказа</span>
          <span className="tabular-nums">{money(total, order.currency)}</span>
        </div>
        {paid > 0 && (
          <div className="flex justify-between text-[var(--success)]">
            <span>Уже оплачено</span>
            <span className="tabular-nums">−{money(paid, order.currency)}</span>
          </div>
        )}
        <div className="flex justify-between text-base font-semibold">
          <span>Остаток к оплате</span>
          <span className="tabular-nums">{money(toPay, order.currency)}</span>
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
              <Badge tone={PAYMENT_STAGE_TONE[p.status] ?? "muted"}>{PAYMENT_STAGE_LABELS[p.status] ?? p.status}</Badge>
            </div>
            {(p.recorded_by_name || p.note) && (
              <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                {[p.recorded_by_name, p.note].filter(Boolean).join(" · ")}
              </div>
            )}
          </div>
          <span
            className={cn(
              "shrink-0 tabular-nums font-semibold",
              p.status === "confirmed" ? "text-[var(--success)]" : "text-[var(--muted-foreground)]",
            )}
          >
            +{money(p.amount, p.currency ?? order.currency)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Карточка заказа в долге ────────────────────────────────────────────── */
/* Детали раскрытой строки: резерв, позиции счёта и списание. */
function DebtOrderDetails({ order }: { order: Order }) {
  const [tab, setTab] = useState("invoice");
  const pending = pendingSum(order);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
        {order.department && <span>{order.department_name ?? order.department}</span>}
        {order.truck_number && <span className="tabular-nums">{order.truck_number}</span>}
        <span>
          Сумма заказа: <b className="tabular-nums">{money(order.total_amount, order.currency)}</b>
        </span>
        <PaidMethodBreakdown order={order} className="text-xs" />
      </div>
      {pending > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">
          <span className="flex items-center gap-1.5 text-[var(--warning)]">
            <Info className="size-4" />
            Уже зарезервировано и ожидает оплаты либо подтверждения
          </span>
          <span className="tabular-nums font-semibold text-[var(--warning)]">{money(pending, order.currency)}</span>
        </div>
      )}
      <Tabs
        tabs={[
          { key: "invoice", label: "Счёт" },
          { key: "writeoff", label: "Списание" },
        ]}
        active={tab}
        onChange={setTab}
      />
      <div>{tab === "invoice" ? <InvoiceTable order={order} /> : <WriteOffList order={order} />}</div>
    </div>
  );
}

/* Заказы в долге — простая таблица: строка раскрывается в детали. */
function DebtOrdersTable({
  orders,
  canPay,
  canViewOrder,
  onPay,
}: {
  orders: Order[];
  canPay: boolean;
  canViewOrder: boolean;
  onPay: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Единый стиль ленивых списков: длинный долг не разворачивается простынёй.
  const [limit, setLimit] = useState(25);
  const visible = orders.slice(0, limit);

  function toggle(id: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
      <Table>
        <THead>
          <TR>
            <TH>Заказ</TH>
            <TH>Создан</TH>
            <TH>Отгружен</TH>
            <TH>Статус</TH>
            <TH className="text-right">Оплачено</TH>
            <TH className="text-right">Остаток</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {visible.map((order) => {
            const open = expanded.has(order.id);
            const status = order.payment_status ?? "unpaid";
            return (
              <Fragment key={order.id}>
                <TR
                  className="cursor-pointer transition-colors hover:bg-[var(--muted)]/40"
                  onClick={() => toggle(order.id)}
                >
                  <TD>
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={(event) => {
                        event.stopPropagation();
                        toggle(order.id);
                      }}
                      className="flex items-center gap-1.5 font-medium"
                    >
                      <ChevronDown
                        className={cn(
                          "size-4 shrink-0 -rotate-90 text-[var(--muted-foreground)] transition-transform",
                          open && "rotate-0",
                        )}
                      />
                      #{order.id}
                    </button>
                  </TD>
                  <TD className="tabular-nums">{formatDateTime(order.created_at)}</TD>
                  <TD className="tabular-nums">{order.shipped_at ? formatDateTime(order.shipped_at) : "—"}</TD>
                  <TD>
                    <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[status] ?? status}
                    </Badge>
                  </TD>
                  <TD className="text-right tabular-nums text-[var(--success)]">
                    {money(order.paid_total, order.currency)}
                  </TD>
                  <TD className="text-right tabular-nums font-semibold text-[var(--destructive)]">
                    {money(remainingOf(order), order.currency)}
                  </TD>
                  <TD onClick={(event) => event.stopPropagation()}>
                    <div className="flex justify-end gap-2">
                      {canViewOrder && (
                        <Link href={`/orders/${order.id}`} className={buttonVariants({ size: "sm", variant: "ghost" })}>
                          Открыть <ExternalLink className="size-3.5" />
                        </Link>
                      )}
                      {canPay && remainingOf(order) > 0 && (
                        <Button size="sm" onClick={() => onPay(order.id)}>
                          Оплатить
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
                {open && (
                  <TR className="bg-[var(--muted)]/30">
                    <TD colSpan={7} className="p-4">
                      <DebtOrderDetails order={order} />
                    </TD>
                  </TR>
                )}
              </Fragment>
            );
          })}
        </TBody>
      </Table>
      <LoadMore
        shown={visible.length}
        total={orders.length}
        hasMore={orders.length > visible.length}
        onClick={() => setLimit((current) => current + 25)}
      />
    </div>
  );
}

/* ── История платежей / счета (из истории клиента) ──────────────────────── */
function PaymentHistoryTable({
  rows,
  emptyText,
  canViewOrders,
}: {
  rows: HistoryPayment[];
  emptyText: string;
  canViewOrders: boolean;
}) {
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
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

/* ── Панель «Внести оплату» ─────────────────────────────────────────────── */
/** Куда выставлять счёт: клиенту онлайн или нашим PDF-документом. */
type InvoiceChannel = "remote" | "document";
type PaymentPart = {
  id: number;
  method: string;
  amount: string;
  phone_number?: string;
  channel?: InvoiceChannel;
};

function PaymentModal({
  open,
  onClose,
  orders,
  selectedId,
  onSelect,
  blockedFor,
  onPaid,
  onError,
}: {
  open: boolean;
  onClose: () => void;
  orders: Order[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  blockedFor: (order: Order) => DebtStore | null;
  onPaid: (msg: string, refresh?: boolean) => Promise<void>;
  onError: (msg: string) => void;
}) {
  const order = orders.find((o) => o.id === selectedId) ?? orders[0];
  const [parts, setParts] = useState<PaymentPart[]>([{ id: 1, method: "cash", amount: "" }]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [payError, setPayError] = useState("");
  const ordersRef = useRef(orders);
  ordersRef.current = orders;

  useEffect(() => {
    if (!open) return;
    const currentOrders = ordersRef.current;
    const currentOrder = currentOrders.find((item) => item.id === selectedId) ?? currentOrders[0];
    const currentAvailable = currentOrder
      ? Math.max(0, moneyCents(remainingOf(currentOrder)) - moneyCents(pendingSum(currentOrder))) / 100
      : 0;
    setParts([{ id: Date.now(), method: "cash", amount: String(currentAvailable) }]);
    setNote("");
  }, [open, selectedId]);

  if (!order) return null;
  const remaining = remainingOf(order);
  const reserved = pendingSum(order);
  const availableCents = Math.max(0, moneyCents(remaining) - moneyCents(reserved));
  const available = availableCents / 100;
  const allocatedCents = parts.reduce((sum, part) => sum + moneyCents(part.amount || 0), 0);
  const allocated = allocatedCents / 100;
  const allowedMethods = order.currency === "KZT" ? [...CASHIER_PAYMENT_METHODS] : (["cash"] as string[]);
  const allPartsValid =
    parts.length > 0 &&
    parts.every((part) => {
      const amount = Number(part.amount);
      const phoneDigits = (part.phone_number ?? order.client_phone ?? "").replace(/\D/g, "");
      // Телефон нужен только онлайн-счёту; наш PDF-документ живёт без него.
      const invoiceOk =
        part.method !== "invoice" || (part.channel ?? "remote") === "document" || [10, 11].includes(phoneDigits.length);
      return (
        Number.isFinite(amount) &&
        amount > 0 &&
        Math.abs(amount * 100 - Math.round(amount * 100)) <= 1e-7 &&
        allowedMethods.includes(part.method) &&
        invoiceOk
      );
    });
  const blockingStore = blockedFor(order);

  function updatePart(id: number, patch: Partial<PaymentPart>) {
    setParts((current) => current.map((part) => (part.id === id ? { ...part, ...patch } : part)));
  }

  function addPart() {
    const used = new Set(parts.map((part) => part.method));
    const method = allowedMethods.find((item) => !used.has(item));
    if (!method) return;
    setParts((current) => [...current, { id: Date.now(), method, amount: "" }]);
  }

  function setQuickTotal(value: number) {
    setParts((current) => {
      if (current.length === 0) return current;
      const totalCents = Math.round(value * 100);
      const base = Math.floor(totalCents / current.length);
      const remainder = totalCents - base * current.length;
      return current.map((part, index) => {
        const cents = base + (index < remainder ? 1 : 0);
        return { ...part, amount: String(cents / 100) };
      });
    });
  }

  async function pay() {
    setBusy(true);
    setPayError("");
    onError("");
    try {
      const payload = parts.map(({ method, amount, phone_number, channel }) => ({
        method,
        amount,
        phone_number,
        channel: method === "invoice" ? (channel ?? "remote") : undefined,
      }));
      const created = await api.post<Payment[]>(`/orders/${order.id}/payments/`, { parts: payload, note });
      const documentInvoice = payload.some((part) => part.channel === "document");
      let documentDownloaded = false;
      if (documentInvoice) {
        // Сразу отдаём кассиру документ на печать; оплата уже создана, поэтому
        // сбой скачивания не должен выглядеть как сбой оплаты.
        try {
          const file = await api.get<Blob>(`/orders/${order.id}/invoice-pdf/`, { responseType: "blob" });
          downloadBlob(file.data, `schet_na_oplatu_${order.id}.pdf`);
          documentDownloaded = true;
        } catch (e) {
          const message = `Оплата создана, но счёт не скачался: ${await blobApiError(e)}`;
          // Ошибку показываем и в модалке: страница лежит под оверлеем.
          setPayError(message);
          onError(message);
        }
      }
      const kaspi = created.data.find((payment) => payment.method === "kaspi");
      const invoice = created.data.find((payment) => payment.method === "invoice");
      // Оплату вносит касса, поэтому полученные деньги закрываются сразу —
      // подтверждать самой себе нечего. Счёт остаётся обязательством клиента.
      const cash = payload.some((part) => part.method === "cash");
      const settled = [cash && "наличные", kaspi && "QR"].filter(Boolean).join(" и ");
      const notice = [
        settled ? `Долг уменьшен: ${settled}.` : "",
        invoice && documentInvoice && documentDownloaded ? "PDF-счёт скачан — распечатайте и передайте клиенту." : "",
        invoice && documentInvoice && !documentDownloaded
          ? "Счёт-часть создана, но PDF не скачался — проверьте реквизиты клиента."
          : "",
        invoice ? "Счёт уменьшит долг, когда деньги поступят." : "",
      ]
        .filter(Boolean)
        .join(" ");
      await onPaid(notice, true);
      onClose();
    } catch (e) {
      // Показ в модалке обязателен: страница с onError лежит под оверлеем,
      // и кассир видел не ошибку, а «кнопка не работает».
      setPayError(apiError(e));
      onError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      eyebrow="Касса · Оплата"
      title="Внести оплату"
      description="Сумма делится на способы построчно; лишнего подтверждать не нужно."
      className="max-w-xl"
      mobileFullscreen
      footer={
        <>
          {payError && (
            <p role="alert" className="mr-auto max-w-sm self-center text-sm text-[var(--destructive)]">
              {payError}
            </p>
          )}
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={busy || !!blockingStore || !allPartsValid || allocatedCents > availableCents}
            onClick={() => void pay()}
          >
            {busy ? "Создание…" : `Внести оплату · ${money(allocated, order.currency)}`}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <>
          {orders.length > 1 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm text-[var(--muted-foreground)]">Заказ</span>
              <Select value={String(order.id)} onValueChange={(v) => onSelect(Number(v))}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {orders.map((o) => (
                    <SelectItem key={o.id} value={String(o.id)}>
                      Заказ #{o.id} — остаток {money(remainingOf(o), o.currency)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-sm text-[var(--muted-foreground)]">Остаток к оплате</div>
              <div className="mt-1 text-[28px] font-bold leading-none tracking-tight tabular-nums text-[var(--destructive)]">
                {money(available, order.currency)}
              </div>
              {reserved > 0 && (
                <div className="mt-1 text-xs text-[var(--muted-foreground)]">
                  Ещё {money(reserved, order.currency)} уже ожидает подтверждения
                </div>
              )}
            </div>
            {!blockingStore && (
              <Button type="button" variant="outline" size="sm" onClick={() => setQuickTotal(available)}>
                Весь долг
              </Button>
            )}
          </div>

          {blockingStore ? (
            <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2.5 text-sm text-[var(--warning)]">
              <Clock className="mt-0.5 size-4 shrink-0" />
              <span>
                Оплата заблокирована: магазин «{blockingStore.name}» платит только по расписанию (
                {formatPaymentSchedule(blockingStore, "payment")}).
              </span>
            </div>
          ) : (
            <>
              {/* Распределение — строгая таблица «Способ | Сумма»: одна строка
                    на способ, без карточек и подсказок на каждый пункт. */}
              <div className="flex flex-col">
                <div className="grid grid-cols-[minmax(0,1fr)_minmax(110px,.7fr)_36px] gap-2 border-b pb-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                  <span>Способ</span>
                  <span>Сумма</span>
                  <span />
                </div>
                {parts.map((part, index) => (
                  <div key={part.id} className="border-b border-[var(--border)]/60 py-2">
                    <div className="grid grid-cols-[minmax(0,1fr)_minmax(110px,.7fr)_36px] items-center gap-2">
                      <Select
                        value={part.method}
                        onValueChange={(method) =>
                          updatePart(part.id, {
                            method,
                            channel: method === "invoice" ? (part.channel ?? "remote") : undefined,
                            phone_number:
                              method === "invoice" ? (part.phone_number ?? order.client_phone ?? "") : undefined,
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {allowedMethods
                            .filter((method) => method === part.method || !parts.some((item) => item.method === method))
                            .map((method) => (
                              <SelectItem key={method} value={method}>
                                {PAYMENT_METHOD_LABELS[method]}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                      <Input
                        type="number"
                        min="0.01"
                        step="0.01"
                        inputMode="decimal"
                        aria-label={`Сумма части ${index + 1}`}
                        placeholder="0"
                        value={part.amount}
                        onChange={(event) => updatePart(part.id, { amount: event.target.value })}
                      />
                      <button
                        type="button"
                        disabled={parts.length === 1}
                        onClick={() => setParts((current) => current.filter((item) => item.id !== part.id))}
                        className="flex size-9 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:text-[var(--destructive)] disabled:opacity-30"
                        aria-label="Удалить способ оплаты"
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                    {part.method === "invoice" && (
                      <div className="mt-2 flex flex-col gap-2">
                        {/* Две выборки счёта: клиенту онлайн или наш документ. */}
                        <div className="grid grid-cols-2 gap-2">
                          <button
                            type="button"
                            aria-pressed={(part.channel ?? "remote") === "remote"}
                            onClick={() =>
                              updatePart(part.id, {
                                channel: "remote",
                                phone_number: part.phone_number ?? order.client_phone ?? "",
                              })
                            }
                            className={cn(
                              "rounded-lg border px-3 py-2 text-left transition-colors",
                              (part.channel ?? "remote") === "remote"
                                ? "border-[var(--foreground)] bg-[var(--muted)]"
                                : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                            )}
                          >
                            <div className="text-sm font-medium">Счёт клиенту</div>
                            <div className="text-[11px] text-[var(--muted-foreground)]">
                              Придёт на телефон, подтвердится сам
                            </div>
                          </button>
                          <button
                            type="button"
                            aria-pressed={part.channel === "document"}
                            onClick={() => updatePart(part.id, { channel: "document" })}
                            className={cn(
                              "rounded-lg border px-3 py-2 text-left transition-colors",
                              part.channel === "document"
                                ? "border-[var(--foreground)] bg-[var(--muted)]"
                                : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                            )}
                          >
                            <div className="text-sm font-medium">Наш PDF-счёт</div>
                            <div className="text-[11px] text-[var(--muted-foreground)]">
                              Скачается для печати, подтверждает касса
                            </div>
                          </button>
                        </div>
                        {(part.channel ?? "remote") === "remote" && (
                          <Input
                            inputMode="tel"
                            aria-label="Телефон для счёта на оплату"
                            placeholder="Телефон клиента для счёта"
                            value={part.phone_number ?? order.client_phone ?? ""}
                            onChange={(event) => updatePart(part.id, { phone_number: event.target.value })}
                          />
                        )}
                      </div>
                    )}
                  </div>
                ))}
                <div className="flex items-center justify-between gap-3 py-2">
                  {parts.length < allowedMethods.length ? (
                    <Button type="button" size="sm" variant="ghost" onClick={addPart}>
                      <Plus className="size-4" /> Добавить способ
                    </Button>
                  ) : (
                    <span />
                  )}
                  <span
                    className={cn(
                      "text-sm font-semibold tabular-nums",
                      allocatedCents > availableCents ? "text-[var(--destructive)]" : "",
                    )}
                  >
                    Итого: {money(allocated, order.currency)}
                  </span>
                </div>
                {allocatedCents > availableCents && (
                  <p className="text-xs text-[var(--destructive)]">Распределение превышает доступный остаток.</p>
                )}
                {!allPartsValid && (
                  <p className="text-xs text-[var(--warning)]">
                    Укажите положительную сумму с точностью до тиына для каждого добавленного способа.
                  </p>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-sm text-[var(--muted-foreground)]">Примечание</span>
                <Input
                  placeholder="Введите примечание (необязательно)"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </div>
            </>
          )}

          <p className="flex items-start gap-2 border-t pt-3 text-xs text-[var(--muted-foreground)]">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            Наличные и QR уменьшат долг сразу — деньги получены на месте. Счёт на оплату уменьшит его позже, когда
            клиент заплатит.
          </p>
        </>
      </div>
    </Modal>
  );
}

/* ── Страница ───────────────────────────────────────────────────────────── */
function ClientDebtPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const isAccountant = can(me, "payments.create");
  const canViewOrders = can(me, "orders.view");
  const { data, loading, error: loadError, reload } = useApi<ClientDebtDetail>(`/clients/${id}/debt-detail/`);
  const {
    data: history,
    error: historyError,
    reload: reloadHistory,
  } = useApi<ClientHistory>(`/clients/${id}/history/`);
  const [tab, setTab] = useState("orders");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [paymentOpen, setPaymentOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const payments = useMemo(() => history?.payments ?? [], [history]);
  const invoices = useMemo(() => payments.filter((p) => p.method === "invoice"), [payments]);

  if (!data) {
    return (
      <AppShell title="Долг клиента">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );
  }

  const debtByCurrency = data.debt_by_currency ?? {};
  const debtCurrency = primaryMoneyCurrency(debtByCurrency, data.debt_currency ?? data.client.currency);
  const debtTotal = amountForCurrency(debtByCurrency, data.debt_total, debtCurrency);
  const lifetimeTotalByCurrency = Object.fromEntries(
    Object.entries(data.lifetime_by_currency ?? {}).map(([currency, totals]) => [currency, totals.total]),
  );
  const lifetimePaidByCurrency = Object.fromEntries(
    Object.entries(data.lifetime_by_currency ?? {}).map(([currency, totals]) => [currency, totals.paid]),
  );
  const lifetimeCurrency = primaryMoneyCurrency(lifetimeTotalByCurrency, debtCurrency);
  const paidCurrency = primaryMoneyCurrency(lifetimePaidByCurrency, lifetimeCurrency);
  const lifetimeTotal = amountForCurrency(lifetimeTotalByCurrency, data.lifetime_total, lifetimeCurrency);
  const lifetimePaid = amountForCurrency(lifetimePaidByCurrency, data.lifetime_paid, paidCurrency);
  const overdueByCurrency = data.overdue_by_currency ?? {};
  const overdueCurrency = primaryMoneyCurrency(overdueByCurrency, debtCurrency);
  const overdueTotal = amountForCurrency(overdueByCurrency, data.overdue_total, overdueCurrency);

  const storeById = new Map(data.stores.map((s) => [s.id, s]));
  // Магазин с расписанием блокирует оплату вне окна.
  function blockedFor(order: Order): DebtStore | null {
    if (order.store == null) return null;
    const s = storeById.get(order.store);
    if (!s || s.payment_schedule_type === "none" || s.window_open) return null;
    return s;
  }

  async function onPaid(msg: string, refresh = true) {
    setNotice(msg);
    if (refresh) {
      await reload();
      reloadHistory();
    }
  }

  function closePaymentModal() {
    setPaymentOpen(false);
    void reload();
    void reloadHistory();
  }

  return (
    <AppShell
      title={`Долг · ${data.client.name}`}
      section="Касса"
      description={data.client.phone ? `Телефон: ${data.client.phone}` : undefined}
      actions={
        <div className="flex items-center gap-2">
          {isAccountant && data.orders.length > 0 && (
            <Button size="sm" onClick={() => setPaymentOpen(true)}>
              <Wallet className="size-4" /> <span className="hidden sm:inline">Внести оплату</span>
            </Button>
          )}
          <Link href="/accounting" className={buttonVariants({ size: "sm", variant: "outline" })}>
            <ArrowLeft className="size-4" />К долгам
          </Link>
        </div>
      }
    >
      {/* Одна денежная полоса вместо четырёх карточек: долг → просрочка →
          оплачено → всего. Зашёл — сразу видно главное. */}
      <Card className="mb-5 flex flex-wrap items-start gap-x-10 gap-y-3 p-4">
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Текущий долг</div>
          <div
            title={money(debtTotal, debtCurrency)}
            className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--destructive)]"
          >
            {compactMoney(debtTotal, debtCurrency)}
          </div>
          <CurrencyRows totals={debtByCurrency} primary={debtCurrency} />
        </div>
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Просрочено</div>
          <div
            title={money(overdueTotal, overdueCurrency)}
            className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--destructive)]"
          >
            {compactMoney(overdueTotal, overdueCurrency)}
          </div>
          <CurrencyRows totals={overdueByCurrency} primary={overdueCurrency} />
        </div>
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Оплачено за всё время</div>
          <div
            title={money(lifetimePaid, paidCurrency)}
            className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--success)]"
          >
            {compactMoney(lifetimePaid, paidCurrency)}
          </div>
          <CurrencyRows totals={lifetimePaidByCurrency} primary={paidCurrency} />
        </div>
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Задолженность за всё время</div>
          <div
            title={money(lifetimeTotal, lifetimeCurrency)}
            className="mt-1 truncate text-lg font-semibold leading-none tabular-nums"
          >
            {compactMoney(lifetimeTotal, lifetimeCurrency)}
          </div>
          <CurrencyRows totals={lifetimeTotalByCurrency} primary={lifetimeCurrency} />
        </div>
      </Card>

      {error && <p className="mb-4 text-sm text-[var(--destructive)]">{error}</p>}
      {notice && (
        <p className="mb-4 rounded-lg border border-[var(--success)]/30 bg-[var(--success)]/10 px-3 py-2 text-sm text-[var(--success)]">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-1 items-start gap-5">
        <div className="flex flex-col gap-4">
          <Tabs
            active={tab}
            onChange={setTab}
            tabs={[
              { key: "orders", label: "Заказы в долге", count: data.orders.length },
              { key: "history", label: "История платежей", count: payments.length },
              { key: "invoices", label: "Счета", count: invoices.length },
            ]}
          />

          {tab !== "orders" && historyError && (
            <ErrorAlert message={historyError} onRetry={() => void reloadHistory()} />
          )}
          {tab === "orders" &&
            (data.orders.length === 0 ? (
              <Card>
                <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                  Долгов нет.
                </CardContent>
              </Card>
            ) : (
              <DebtOrdersTable
                orders={data.orders}
                canPay={isAccountant}
                canViewOrder={canViewOrders}
                onPay={(id) => {
                  setSelectedId(id);
                  setPaymentOpen(true);
                }}
              />
            ))}
          {tab === "history" && (
            <PaymentHistoryTable rows={payments} emptyText="Платежей пока нет." canViewOrders={canViewOrders} />
          )}
          {tab === "invoices" && (
            <PaymentHistoryTable rows={invoices} emptyText="Выставленных счетов нет." canViewOrders={canViewOrders} />
          )}
        </div>
      </div>
      {isAccountant && data.orders.length > 0 && (
        <PaymentModal
          open={paymentOpen}
          onClose={closePaymentModal}
          orders={data.orders}
          selectedId={selectedId ?? data.orders[0]?.id ?? null}
          onSelect={setSelectedId}
          blockedFor={blockedFor}
          onPaid={onPaid}
          onError={setError}
        />
      )}
    </AppShell>
  );
}

export default function ClientDebtPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm="reports.view" title="Долг клиента">
      <ClientDebtPageInner {...props} />
    </RequirePerm>
  );
}
