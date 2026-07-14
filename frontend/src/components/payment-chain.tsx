"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import {
  PAYMENT_STAGE_LABELS, PAYMENT_STAGE_TONE, PAYMENT_METHOD_LABELS,
} from "@/lib/constants";
import { HandCoins, ReceiptText } from "lucide-react";
import type { Me, Order, Payment } from "@/lib/types";

/** Открыт ли приём оплат по заказу: Отдел 2 — с момента заявки, Отдел 1 — после отгрузки. */
export function paymentOpen(order: Order): boolean {
  if (order.department === "field") {
    return !["draft", "rejected", "cancelled"].includes(order.status);
  }
  return order.status === "shipped";
}

export function PaymentStageBadge({ status }: { status: string }) {
  return (
    <Badge tone={PAYMENT_STAGE_TONE[status] ?? "muted"} dot>
      {PAYMENT_STAGE_LABELS[status] ?? status}
    </Badge>
  );
}

function StageTrace({ p }: { p: Payment }) {
  const steps = [
    { label: "Создана", by: p.recorded_by_name, at: p.paid_at },
    { label: "Принята", by: p.received_by_name, at: p.received_at },
    { label: "Подтверждена", by: p.confirmed_by_name, at: p.confirmed_at },
  ].filter((s) => s.by || s.at);
  return (
    <div className="flex flex-col gap-0.5 text-[11px] text-[var(--muted-foreground)]">
      {steps.map((s) => (
        <span key={s.label}>
          {s.label}: {s.by ?? "—"}{s.at ? ` · ${new Date(s.at).toLocaleString("ru-RU")}` : ""}
        </span>
      ))}
    </div>
  );
}

/**
 * Оплаты заказа в цепочке подтверждения с действиями по правам:
 * приём (payments.create) → подтверждение бухгалтером-кассой (payments.confirm).
 */
export function PaymentChain({ order, me, onChanged }: {
  order: Order; me: Me | null; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const payments = order.pending_payments ?? [];
  if (payments.length === 0) return null;

  async function act(url: string) {
    setBusy(true); setError("");
    try { await api.post(url); onChanged(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col gap-3">
      {payments.map((p) => (
        <div key={p.id} className="flex flex-col gap-2 rounded-lg border p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-base font-semibold tabular-nums">
              <ReceiptText className="size-4 text-[var(--warning)]" />
              {formatMoney(p.amount)} ₸
              <span className="text-xs font-normal text-[var(--muted-foreground)]">
                {p.method_label || PAYMENT_METHOD_LABELS[p.method] || p.method}
              </span>
            </div>
            <PaymentStageBadge status={p.status} />
          </div>
          <StageTrace p={p} />
          <div className="flex flex-wrap gap-2">
            {p.status === "requested" && can(me, "payments.create") && (
              <Button size="sm" disabled={busy}
                onClick={() => act(`/orders/${order.id}/payments/${p.id}/receive/`)}>
                Деньги получены
              </Button>
            )}
            {p.status === "received" && can(me, "payments.confirm") && (
              <Button size="sm" disabled={busy}
                onClick={() => act(`/orders/${order.id}/payments/${p.id}/confirm/`)}>
                Подтвердить оплату
              </Button>
            )}
            {can(me, "payments.confirm") && (
              <Button size="sm" variant="ghost" disabled={busy}
                onClick={() => act(`/orders/${order.id}/payments/${p.id}/reject/`)}>
                Отклонить
              </Button>
            )}
          </div>
        </div>
      ))}
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

/**
 * Кнопки старта цепочки: «Запросить оплату» (счёт выставлен) и
 * «Принять оплату» (деньги получены с выезда). Требует payments.create.
 */
export function AddPaymentActions({ order, me, onChanged }: {
  order: Order; me: Me | null; onChanged: () => void;
}) {
  const [stage, setStage] = useState<"requested" | "received" | null>(null);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!can(me, "payments.create") || !paymentOpen(order)) return null;
  const remaining = Number(order.remaining_amount ??
    (Number(order.total_amount) - Number(order.paid_total)));
  if (remaining <= 0) return null;

  function open(s: "requested" | "received") {
    setStage(s); setAmount(String(remaining)); setMethod("cash"); setError("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post(`/orders/${order.id}/payments/`, { amount, method, stage });
      setStage(null);
      onChanged();
    } catch (err) { setError(apiError(err)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => open("requested")}>
          <ReceiptText className="size-4" /> Запросить оплату
        </Button>
        <Button size="sm" onClick={() => open("received")}>
          <HandCoins className="size-4" /> Принять оплату
        </Button>
      </div>
      <Modal open={stage !== null} onClose={() => setStage(null)}
        eyebrow={`Заказ #${order.id} · ${order.client_name ?? ""}`}
        title={stage === "requested" ? "Запросить оплату" : "Принять оплату"}
        description={stage === "requested"
          ? "Клиенту выставлен счёт — оплата появится в цепочке подтверждения."
          : "Деньги получены от клиента. Далее — подтверждение кассой."}
        className="max-w-sm">
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="grid gap-2">
            <Label>Сумма (остаток {formatMoney(String(remaining))} ₸)</Label>
            <Input type="number" min="1" step="0.01" value={amount} autoFocus
              onChange={(e) => setAmount(e.target.value)} required />
          </div>
          <div className="grid gap-2">
            <Label>Способ</Label>
            <Select value={method} onChange={(e) => setMethod(e.target.value)}>
              {Object.entries(PAYMENT_METHOD_LABELS)
                .filter(([k]) => k !== "debt")
                .map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </div>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setStage(null)}>Отмена</Button>
            <Button type="submit" disabled={busy || Number(amount) <= 0}>
              {busy ? "Сохранение…" : stage === "requested" ? "Запросить" : "Принять"}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
