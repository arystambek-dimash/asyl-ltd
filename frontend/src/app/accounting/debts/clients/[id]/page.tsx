"use client";
import { use, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { Modal } from "@/components/ui/modal";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Tabs } from "@/components/ui/tabs";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate } from "@/components/ui/data-state";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, formatMoney, formatDateTime } from "@/lib/utils";
import { can } from "@/lib/can";
import { PaidMethodBreakdown } from "@/components/payment-chain";
import { useAuth } from "@/store/auth";
import {
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONE,
  CASHIER_PAYMENT_METHOD_LABELS,
  CASHIER_PAYMENT_METHODS,
  PAYMENT_STAGE_LABELS,
  PAYMENT_STAGE_TONE,
  PAYMENT_METHOD_LABELS,
} from "@/lib/constants";
import {
  ArrowLeft,
  Building2,
  Calendar,
  ChevronDown,
  Clock,
  ExternalLink,
  FileText,
  Info,
  Plus,
  ShieldCheck,
  Trash2,
  Truck,
  Wallet,
} from "lucide-react";
import type { Client, Order, Payment } from "@/lib/types";

const money = formatCurrency;

function compactMoney(value: number | string) {
  const amount = Number(value);
  return `${new Intl.NumberFormat("ru-RU", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Number.isFinite(amount) ? amount : 0)} ₸`;
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
  id: number;
  order_id: number;
  date: string;
  employee: string | null;
  method: string;
  status: string;
  amount: string;
}
interface ClientHistory {
  payments: HistoryPayment[];
}

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
            <TH className="text-right">Цена за единицу, ₸</TH>
            <TH className="text-right">Итого</TH>
          </TR>
        </THead>
        <TBody>
          {lines.map((l) => (
            <TR key={l.key}>
              <TD className="font-medium">{l.label}</TD>
              <TD className="text-right tabular-nums">{l.qty}</TD>
              <TD className="text-right tabular-nums">{money(l.price)}</TD>
              <TD className="text-right tabular-nums font-medium">{money(l.total)}</TD>
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
            +{money(p.amount)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Карточка заказа в долге ────────────────────────────────────────────── */
function OrderDebtCard({ order, canPay, onPay }: { order: Order; canPay: boolean; onPay: () => void }) {
  const [tab, setTab] = useState("invoice");
  const [expanded, setExpanded] = useState(false);
  const status = order.payment_status ?? "unpaid";
  const pct = Math.min(100, Math.round((Number(order.paid_total) / Math.max(1, Number(order.total_amount))) * 100));
  const pending = pendingSum(order);
  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-4 sm:pt-5">
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
          <div className="flex shrink-0 items-center gap-2">
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Остаток</div>
              <div className="tabular-nums text-base font-bold text-[var(--destructive)]">
                {money(remainingOf(order), order.currency)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-label={expanded ? "Свернуть заказ" : "Развернуть заказ"}
              className="flex size-10 items-center justify-center rounded-xl border text-[var(--muted-foreground)] transition hover:bg-[var(--accent)]"
            >
              <ChevronDown className={cn("size-4 transition-transform", expanded && "rotate-180")} />
            </button>
          </div>
        </div>

        {expanded && (
          <>
            <div className="grid grid-cols-2 gap-4 border-t pt-4 sm:grid-cols-4 sm:divide-x sm:[&>div+div]:pl-4">
              <div>
                <div className="text-xs text-[var(--muted-foreground)]">Сумма заказа</div>
                <div className="mt-0.5 tabular-nums font-semibold">{money(order.total_amount, order.currency)}</div>
              </div>
              <div>
                <div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
                <div className="mt-0.5 tabular-nums font-semibold text-[var(--success)]">
                  {money(order.paid_total, order.currency)}
                </div>
                <PaidMethodBreakdown order={order} className="mt-0.5 text-[11px]" />
              </div>
              <div>
                <div className="text-xs text-[var(--muted-foreground)]">Остаток долга</div>
                <div className="mt-0.5 tabular-nums font-semibold text-[var(--destructive)]">
                  {money(remainingOf(order), order.currency)}
                </div>
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
                  Уже зарезервировано и ожидает оплаты либо подтверждения
                </span>
                <span className="tabular-nums font-semibold text-[var(--warning)]">
                  {money(pending, order.currency)}
                </span>
              </div>
            )}

            <div className="border-t pt-1">
              <Tabs
                tabs={[
                  { key: "invoice", label: "Счёт" },
                  { key: "writeoff", label: "Списание" },
                ]}
                active={tab}
                onChange={setTab}
              />
              <div className="pt-3">
                {tab === "invoice" ? <InvoiceTable order={order} /> : <WriteOffList order={order} />}
              </div>
            </div>
          </>
        )}

        <div className="flex flex-col gap-2 border-t pt-3 sm:flex-row sm:justify-end">
          <Link href={`/orders/${order.id}`}>
            <Button size="sm" variant="outline" className="w-full sm:w-auto">
              Открыть заказ <ExternalLink className="size-3.5" />
            </Button>
          </Link>
          {canPay && remainingOf(order) > 0 && (
            <Button size="sm" onClick={onPay} className="w-full sm:w-auto">
              <Wallet className="size-4" /> Внести оплату
            </Button>
          )}
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
                <TD
                  className={cn(
                    "text-right tabular-nums font-semibold",
                    p.status === "confirmed" && "text-[var(--success)]",
                  )}
                >
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

type PaymentPart = { id: number; method: string; amount: string; phone_number?: string };

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
  const [kaspiQr, setKaspiQr] = useState<Payment["provider"] | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [qrImageFailed, setQrImageFailed] = useState(false);
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
    setKaspiQr(null);
    setReviewing(false);
    setQrImageFailed(false);
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
      return (
        Number.isFinite(amount) &&
        amount > 0 &&
        Math.abs(amount * 100 - Math.round(amount * 100)) <= 1e-7 &&
        allowedMethods.includes(part.method) &&
        (part.method !== "invoice" || [10, 11].includes(phoneDigits.length))
      );
    });
  const blockingStore = blockedFor(order);
  const quickValue = (f: number) => Math.round(available * f * 100) / 100;

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
    onError("");
    try {
      const payload = parts.map(({ method, amount, phone_number }) => ({ method, amount, phone_number }));
      const created = await api.post<Payment[]>(`/orders/${order.id}/payments/`, { parts: payload, note });
      const kaspi = created.data.find((payment) => payment.method === "kaspi");
      const invoice = created.data.find((payment) => payment.method === "invoice");
      if (kaspi) setKaspiQr(kaspi.provider ?? null);
      setReviewing(false);
      const notice = [
        kaspi ? `QR по заказу #${order.id} создан.` : "",
        invoice ? "Счёт на оплату отправлен клиенту." : "",
        payload.some((part) => part.method === "cash") ? "Наличная часть добавлена на подтверждение кассиру." : "",
      ]
        .filter(Boolean)
        .join(" ");
      // Keep the just-created QR visible until the operator explicitly closes
      // the modal. A successful refresh can remove a fully paid order from
      // this debt list and would otherwise unmount the QR immediately.
      await onPaid(notice, !kaspi);
      if (!kaspi) onClose();
    } catch (e) {
      onError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      eyebrow="Касса · Смешанная оплата"
      title="Внести оплату"
      description="Разделите сумму между наличными, QR и счётом. Онлайн-части подтвердятся автоматически после оплаты."
      className="max-w-xl"
      mobileFullscreen
      footer={
        kaspiQr ? (
          <Button onClick={onClose}>Готово</Button>
        ) : reviewing ? (
          <>
            <Button variant="outline" disabled={busy} onClick={() => setReviewing(false)}>
              Назад
            </Button>
            <Button disabled={busy} onClick={() => void pay()}>
              {busy ? "Создание…" : `Подтвердить · ${money(allocated, order.currency)}`}
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" disabled={busy} onClick={onClose}>
              Отмена
            </Button>
            <Button
              disabled={busy || !!blockingStore || !allPartsValid || allocatedCents > availableCents}
              onClick={() => setReviewing(true)}
            >
              Проверить оплату · {money(allocated, order.currency)}
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-5">
        {kaspiQr ? (
          <div className="text-center">
            <div className="text-lg font-semibold">Kaspi QR готов</div>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              Покажите QR клиенту или откройте оплату на его устройстве.
            </p>
            {kaspiQr.qr_image_url && !qrImageFailed ? (
              <Image
                src={kaspiQr.qr_image_url}
                alt="Kaspi QR"
                width={256}
                height={256}
                unoptimized
                onError={() => setQrImageFailed(true)}
                className="mx-auto mt-4 size-64 rounded-2xl bg-white p-2 shadow-sm"
              />
            ) : (
              <div className="mx-auto mt-4 flex size-64 flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--muted)]/35 p-6">
                <FileText className="size-10 text-[var(--muted-foreground)]" />
                <p className="mt-3 text-sm text-[var(--muted-foreground)]">
                  Изображение QR недоступно. Откройте оплату кнопкой ниже.
                </p>
              </div>
            )}
            {kaspiQr.qr_token_url && (
              <Button className="mt-4 w-full" onClick={() => window.open(kaspiQr.qr_token_url!, "_blank", "noopener")}>
                Открыть Kaspi
              </Button>
            )}
          </div>
        ) : reviewing ? (
          <div className="space-y-4">
            <div className="rounded-xl border bg-[var(--muted)]/30 p-4">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                Проверьте перед созданием
              </div>
              <div className="mt-1 text-3xl font-bold tabular-nums">{money(allocated, order.currency)}</div>
              <div className="mt-1 text-sm text-[var(--muted-foreground)]">
                Заказ #{order.id} · доступно {money(available, order.currency)}
              </div>
            </div>
            <div className="space-y-2">
              {parts.map((part) => (
                <div key={part.id} className="flex items-start justify-between gap-4 rounded-lg border px-3 py-2.5">
                  <div>
                    <div className="font-medium">{CASHIER_PAYMENT_METHOD_LABELS[part.method] ?? part.method}</div>
                    {part.method === "invoice" && (
                      <div className="text-xs text-[var(--muted-foreground)]">
                        Телефон: {part.phone_number ?? order.client_phone}
                      </div>
                    )}
                    <div className="text-xs text-[var(--muted-foreground)]">
                      {part.method === "cash"
                        ? "Потребует подтверждения кассиром"
                        : "Подтвердится автоматически после оплаты"}
                    </div>
                  </div>
                  <div className="shrink-0 font-semibold tabular-nums">{money(part.amount, order.currency)}</div>
                </div>
              ))}
            </div>
            {note && (
              <div className="rounded-lg border px-3 py-2 text-sm">
                <span className="text-[var(--muted-foreground)]">Примечание: </span>
                {note}
              </div>
            )}
            <p className="text-xs text-[var(--muted-foreground)]">
              После подтверждения наличная часть попадёт в очередь кассира, а QR и счёт сразу будут созданы у платёжного
              сервиса.
            </p>
          </div>
        ) : (
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

            {blockingStore ? (
              <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2.5 text-sm text-[var(--warning)]">
                <Clock className="mt-0.5 size-4 shrink-0" />
                <span>
                  Оплата заблокирована: магазин «{blockingStore.name}» платит только по расписанию (
                  {scheduleLabel(blockingStore)}).
                </span>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-4 gap-2">
                  {QUICK_FRACTIONS.map(({ label, f }) => {
                    const v = quickValue(f);
                    const active = Math.abs(allocated - v) < 0.001;
                    return (
                      <button
                        key={label}
                        type="button"
                        onClick={() => setQuickTotal(v)}
                        className={cn(
                          "flex flex-col items-center gap-0.5 rounded-lg border px-1 py-2 transition-colors",
                          active
                            ? "border-[var(--foreground)] bg-[var(--muted)]"
                            : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                        )}
                      >
                        <span className="text-sm font-semibold">{label}</span>
                        <span className="text-[11px] tabular-nums text-[var(--muted-foreground)]">
                          {formatMoney(v)} {order.currency === "USD" ? "$" : "₸"}
                        </span>
                      </button>
                    );
                  })}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">Распределение</span>
                    <span
                      className={cn(
                        "text-xs tabular-nums",
                        allocatedCents > availableCents
                          ? "text-[var(--destructive)]"
                          : "text-[var(--muted-foreground)]",
                      )}
                    >
                      {money(allocated, order.currency)} из {money(available, order.currency)}
                    </span>
                  </div>
                  {parts.map((part, index) => (
                    <div key={part.id} className="rounded-xl border bg-[var(--card)] p-2.5">
                      <div className="grid grid-cols-[minmax(0,1fr)_minmax(100px,.75fr)_40px] gap-2">
                        <Select
                          value={part.method}
                          onValueChange={(method) =>
                            updatePart(part.id, {
                              method,
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
                              .filter(
                                (method) => method === part.method || !parts.some((item) => item.method === method),
                              )
                              .map((method) => (
                                <SelectItem key={method} value={method}>
                                  {CASHIER_PAYMENT_METHOD_LABELS[method]}
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
                          className="flex size-10 items-center justify-center rounded-md border text-[var(--muted-foreground)] hover:text-[var(--destructive)] disabled:opacity-30"
                          aria-label="Удалить способ оплаты"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                      {part.method === "invoice" && (
                        <div className="mt-2">
                          <Input
                            inputMode="tel"
                            aria-label="Телефон для счёта на оплату"
                            placeholder="Телефон клиента, например 8 700 000 00 00"
                            value={part.phone_number ?? order.client_phone ?? ""}
                            onChange={(event) => updatePart(part.id, { phone_number: event.target.value })}
                          />
                          <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                            Счёт будет отправлен на этот номер и подтвердится автоматически.
                          </p>
                        </div>
                      )}
                      {part.method === "kaspi" && (
                        <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
                          QR появится после создания и подтвердится автоматически после оплаты.
                        </p>
                      )}
                    </div>
                  ))}
                  {parts.length < allowedMethods.length && (
                    <Button type="button" variant="outline" className="w-full" onClick={addPart}>
                      <Plus className="size-4" /> Добавить способ оплаты
                    </Button>
                  )}
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
              Наличные уменьшат долг после подтверждения кассиром. QR и счёт — автоматически после оплаты клиентом.
            </p>
          </>
        )}
      </div>
    </Modal>
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
          <Link href="/accounting">
            <Button size="sm" variant="outline">
              <ArrowLeft className="size-4" />К долгам
            </Button>
          </Link>
        </div>
      }
    >
      <section className="mb-5 grid grid-cols-2 gap-2 sm:gap-3 xl:grid-cols-4">
        <StatCard
          label="Текущий долг"
          tone="destructive"
          caption="к погашению"
          value={<span title={money(data.debt_total)}>{compactMoney(data.debt_total)}</span>}
        />
        <StatCard
          label="Общая задолженность"
          caption="всего за всё время"
          value={<span title={money(data.lifetime_total)}>{compactMoney(data.lifetime_total)}</span>}
        />
        <StatCard
          label="Оплачено"
          tone="success"
          caption="всего оплачено"
          value={<span title={money(data.lifetime_paid)}>{compactMoney(data.lifetime_paid)}</span>}
        />
        <StatCard
          label="Просрочено"
          tone="destructive"
          caption="просроченные суммы"
          value={<span title={money(data.overdue_total)}>{compactMoney(data.overdue_total)}</span>}
        />
      </section>

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

          {tab === "orders" &&
            (data.orders.length === 0 ? (
              <Card>
                <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                  Долгов нет.
                </CardContent>
              </Card>
            ) : (
              data.orders.map((order) => (
                <OrderDebtCard
                  key={order.id}
                  order={order}
                  canPay={isAccountant}
                  onPay={() => {
                    setSelectedId(order.id);
                    setPaymentOpen(true);
                  }}
                />
              ))
            ))}
          {tab === "history" && <PaymentHistoryTable rows={payments} emptyText="Платежей пока нет." />}
          {tab === "invoices" && <PaymentHistoryTable rows={invoices} emptyText="Выставленных счетов нет." />}
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
