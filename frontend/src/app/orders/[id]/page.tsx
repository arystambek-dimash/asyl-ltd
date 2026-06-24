"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import {
  ResponsiveContainer, RadialBarChart, RadialBar, PolarAngleAxis,
  BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from "recharts";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import { formatMoney } from "@/lib/utils";
import { ORDER_STATUS_LABELS } from "@/lib/constants";
import { CheckCircle2, Circle } from "lucide-react";
import type { Order, Payment } from "@/lib/types";

const ORDER_STATUSES = ["draft", "confirmed", "paid", "arrived", "loading", "loaded", "shipped", "cancelled"];
const LIFECYCLE = ["draft", "confirmed", "paid", "arrived", "loading", "loaded", "shipped"];

function OrderStepper({ status }: { status: string }) {
  if (status === "cancelled") {
    return <span className="text-sm font-medium text-[var(--destructive)]">Заказ отменён</span>;
  }
  const current = LIFECYCLE.indexOf(status);
  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-2">
      {LIFECYCLE.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={s} className="flex items-center">
            <div className="flex items-center gap-1.5">
              {done
                ? <CheckCircle2 className="size-4 text-[var(--success)]" />
                : <Circle className={cn("size-4", active ? "text-[var(--ring)]" : "text-[var(--muted-foreground)]/40")}
                    {...(active ? { fill: "currentColor", fillOpacity: 0.15 } : {})} />}
              <span className={cn("text-[11px]",
                active ? "font-medium text-[var(--foreground)]"
                  : done ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
                {ORDER_STATUS_LABELS[s] ?? s}
              </span>
            </div>
            {i < LIFECYCLE.length - 1 && (
              <div className={cn("mx-1.5 h-0.5 w-5 rounded-full",
                i < current ? "bg-[var(--success)]" : "bg-[var(--border)]")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function OrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const { data: order, reload } = useApi<Order>(`/orders/${id}/`);
  const { reload: reloadPay } = useApi<Payment[]>(null);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isManager = can(me, "orders.confirm");
  const isAccountant = can(me, "payments.create");
  const canEditStatus = can(me, "orders.edit");
  const [newStatus, setNewStatus] = useState("");

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await reload(); reloadPay(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  if (!order) return <AppShell title="Заказ"><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;

  const total = Number(order.total_amount);
  const paid = Number(order.paid_total);
  const remaining = total - paid;
  const paidPct = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;

  const hasShipment = order.weigh_in_kg != null;
  const counted = order.bags_loaded ?? 0;
  const ordered = order.items.reduce((s, it) => s + Number(it.quantity), 0);

  return (
    <AppShell title={`Заказ #${order.id}`}>
      {/* шапка со степпером */}
      <div className="mb-6 flex flex-col gap-3 rounded-xl border bg-[var(--card)] p-5 shadow-card">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-lg font-bold tracking-tight">Заказ #{order.id}</div>
            <div className="text-sm text-[var(--muted-foreground)]">
              {order.client_name || "—"}{order.truck_number ? ` · ${order.truck_number}` : ""}
            </div>
          </div>
          <StatusBadge status={order.status} dot />
        </div>
        <div className="border-t pt-3"><OrderStepper status={order.status} /></div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* левая колонка */}
        <div className="flex flex-col gap-6 lg:col-span-2">
          <Card>
            <CardHeader><CardTitle>Позиции</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <THead><TR><TH>Товар</TH><TH>Мешков</TH></TR></THead>
                <TBody>
                  {order.items.map((it, i) => (
                    <TR key={i}>
                      <TD className="font-medium">{it.product_label || `Товар #${it.product}`}</TD>
                      <TD className="tabular-nums">{it.quantity}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              <div className="mt-4 flex items-center justify-between border-t pt-4">
                <span className="text-sm text-[var(--muted-foreground)]">Сумма заказа</span>
                <span className="text-lg font-bold tabular-nums">{formatMoney(order.total_amount)} ₸</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Оплата</CardTitle></CardHeader>
            <CardContent>
              <div className="flex flex-col items-center gap-4 sm:flex-row sm:gap-6">
                <div className="relative h-[160px] w-[160px] shrink-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadialBarChart innerRadius="72%" outerRadius="100%" startAngle={90} endAngle={-270}
                      data={[{ name: "paid", value: paidPct, fill: "var(--success)" }]}>
                      <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                      <RadialBar background dataKey="value" cornerRadius={8} />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-2xl font-bold tabular-nums">{paidPct}%</span>
                    <span className="text-[11px] text-[var(--muted-foreground)]">оплачено</span>
                  </div>
                </div>
                <div className="flex flex-1 flex-col gap-2 self-stretch">
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--muted-foreground)]">Сумма</span>
                    <span className="tabular-nums font-medium">{formatMoney(order.total_amount)} ₸</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--muted-foreground)]">Оплачено</span>
                    <span className="tabular-nums text-[var(--success)]">{formatMoney(order.paid_total)} ₸</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-[var(--muted-foreground)]">Остаток</span>
                    <span className={cn("tabular-nums font-medium", remaining > 0 && "text-[var(--destructive)]")}>
                      {formatMoney(String(remaining))} ₸</span>
                  </div>
                  {isAccountant && remaining > 0 && (
                    <div className="mt-1 flex gap-2 border-t pt-3">
                      <Input type="number" placeholder="Сумма" value={amount}
                        onChange={(e) => setAmount(e.target.value)} />
                      <Button size="sm" disabled={busy || !amount}
                        onClick={() => act(async () => {
                          await api.post(`/orders/${order.id}/payments/`, { amount });
                          setAmount("");
                        })}>Внести</Button>
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {order.status === "shipped" && (
            <Card>
              <CardHeader><CardTitle>Мешки</CardTitle></CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={[
                    { name: "Заказано", value: ordered, fill: "var(--muted-foreground)" },
                    { name: "Камера", value: counted, fill: "var(--ring)" },
                  ]} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 12, fill: "var(--muted-foreground)" }} />
                    <YAxis tick={{ fontSize: 12, fill: "var(--muted-foreground)" }} allowDecimals={false} />
                    <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.4 }}
                      contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
                    <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                      {["a", "b"].map((k, i) => (
                        <Cell key={k} fill={["var(--muted-foreground)", "var(--ring)"][i]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  Камера посчитала {counted} меш.; заказано {ordered} меш.
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* правая колонка */}
        <div className="flex flex-col gap-6">
          {hasShipment && (
            <Card>
              <CardHeader><CardTitle>Вес</CardTitle></CardHeader>
              <CardContent className="flex flex-col gap-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[var(--muted-foreground)]">Вес КАМАЗа</span>
                  <span className="tabular-nums font-medium">{order.weigh_in_kg ? `${formatMoney(order.weigh_in_kg)} кг` : "—"}</span>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader><CardTitle>Действия</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {isManager && order.status === "draft" && (
                <Button variant="outline" disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${order.id}/confirm/`))}>
                  Подтвердить заказ
                </Button>
              )}
              {order.status === "paid" && (
                <p className="text-sm text-[var(--success)]">Заказ оплачен, готов к отгрузке.</p>
              )}
              {order.status === "shipped" && (
                <p className="text-sm text-[var(--muted-foreground)]">Заказ отгружен.</p>
              )}
              {!isManager && order.status === "draft" && (
                <p className="text-sm text-[var(--muted-foreground)]">Ожидает подтверждения менеджером.</p>
              )}
              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            </CardContent>
          </Card>

          {canEditStatus && (
            <Card>
              <CardHeader><CardTitle>Сменить статус</CardTitle></CardHeader>
              <CardContent className="flex flex-col gap-3">
                <p className="text-xs text-[var(--muted-foreground)]">
                  Ручная смена статуса для исправления ошибок.
                </p>
                <div className="flex flex-col gap-1.5">
                  <Label>Новый статус</Label>
                  <Select value={newStatus || order.status}
                    onChange={(e) => setNewStatus(e.target.value)}>
                    {ORDER_STATUSES.map((s) => (
                      <option key={s} value={s}>{ORDER_STATUS_LABELS[s] ?? s}</option>
                    ))}
                  </Select>
                </div>
                <Button size="sm" variant="outline"
                  disabled={busy || !newStatus || newStatus === order.status}
                  onClick={() => act(async () => {
                    await api.post(`/orders/${order.id}/set-status/`, { status: newStatus });
                    setNewStatus("");
                  })}>
                  Применить
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </AppShell>
  );
}
