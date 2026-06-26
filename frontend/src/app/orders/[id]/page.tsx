"use client";
import { use, useState, useEffect } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { CollapsibleCard } from "@/components/ui/collapsible-card";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
} from "recharts";
import Link from "next/link";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import { formatMoney } from "@/lib/utils";
import { ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { CheckCircle2, Circle, Layers, Package, Scale, Truck, Boxes } from "lucide-react";
import type { Order, Payment } from "@/lib/types";

const ORDER_STATUSES = ["draft", "pending", "confirmed", "arrived", "loading", "loaded", "shipped", "cancelled"];
const LIFECYCLE = ["draft", "pending", "confirmed", "arrived", "loading", "loaded", "shipped"];

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

function OrderDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const { data: order, reload } = useApi<Order>(`/orders/${id}/`);
  const { reload: reloadPay } = useApi<Payment[]>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isManager = can(me, "orders.confirm");
  const canEditStatus = can(me, "orders.edit");
  const canViewStatus = can(me, "orders.view");
  const [newStatus, setNewStatus] = useState("");
  // Цены за мешок по позиции (для подтверждения). Предзаполняются ценой клиента.
  const [prices, setPrices] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!order) return;
    if (order.status !== "pending" && order.status !== "draft") return;
    const init: Record<number, string> = {};
    for (const it of order.items) {
      if (it.id == null) continue;
      const hint = it.unit_price ?? it.client_price;
      init[it.id] = hint != null ? String(hint) : "";
    }
    setPrices(init);
  }, [order]);

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

  const hasShipment = order.weigh_in_kg != null;
  const counted = order.bags_loaded ?? 0;
  const ordered = order.items.reduce((s, it) => s + Number(it.quantity), 0);
  const itemsWeight = order.items.reduce((s, it) => s + Number(it.quantity) * Number(it.weight_kg ?? 0), 0);

  const isNew = order.status === "draft" || order.status === "pending";
  // Подтверждение с ценами рендерится отдельной карточкой (заказы с позициями).
  const confirmInPriceCard = isManager && isNew && order.items.length > 0;
  // «Действия» имеет смысл, только если есть что показать в текущем состоянии.
  const hasActions =
    (isManager && isNew && !confirmInPriceCard) || order.status === "shipped" || (!isManager && isNew);
  const pendingReqs = order.pending_status_requests ?? [];

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
          <div className="flex items-center gap-2">
            <Badge tone="muted">{order.transport_type === "train" ? "🚂 Поезд" : "🚚 Трак"}</Badge>
            <StatusBadge status={order.status} dot />
            {order.status === "shipped" && order.payment_status && (
              <Badge tone={PAYMENT_STATUS_TONE[order.payment_status] ?? "muted"} dot>
                {PAYMENT_STATUS_LABELS[order.payment_status] ?? order.payment_status}
              </Badge>
            )}
          </div>
        </div>
        <div className="border-t pt-3"><OrderStepper status={order.status} /></div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* левая колонка */}
        <div className="flex flex-col gap-6 lg:col-span-2">
          <Card>
            <CardHeader><CardTitle>Позиции</CardTitle></CardHeader>
            <CardContent>
              {/* сводка */}
              <div className="mb-4 grid grid-cols-3 gap-3">
                <div className="rounded-lg border bg-[var(--muted)]/30 p-3">
                  <div className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]"><Layers className="size-3.5" /> Позиций</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{order.items.length}</div>
                </div>
                <div className="rounded-lg border bg-[var(--muted)]/30 p-3">
                  <div className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]"><Package className="size-3.5" /> Всего мешков</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{ordered}</div>
                </div>
                <div className="rounded-lg border bg-[var(--muted)]/30 p-3">
                  <div className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]"><Scale className="size-3.5" /> Вес груза</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{formatMoney(String(itemsWeight))} кг</div>
                </div>
              </div>

              <Table>
                <THead><TR><TH>Товар</TH><TH className="text-right">Мешков</TH><TH className="text-right">Цена</TH><TH className="text-right">Сумма</TH></TR></THead>
                <TBody>
                  {order.items.map((it, i) => {
                    const price = Number(it.price ?? 0);
                    const sum = price * Number(it.quantity);
                    return (
                      <TR key={i}>
                        <TD className="font-medium">{it.product_label || `Товар #${it.product}`}
                          {it.weight_kg && <span className="block text-xs text-[var(--muted-foreground)]">{it.weight_kg} кг/мешок</span>}
                        </TD>
                        <TD className="text-right tabular-nums">{it.quantity}</TD>
                        <TD className="text-right tabular-nums text-[var(--muted-foreground)]">{price ? `${formatMoney(it.price!)} ₸` : "—"}</TD>
                        <TD className="text-right tabular-nums font-medium">{formatMoney(String(sum))} ₸</TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
              <div className="mt-4 flex items-center justify-between border-t pt-4">
                <span className="text-sm text-[var(--muted-foreground)]">Сумма заказа</span>
                <span className="text-lg font-bold tabular-nums">{formatMoney(order.total_amount)} ₸</span>
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
              <CardContent className="flex flex-col gap-3">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5 text-[var(--muted-foreground)]"><Truck className="size-4" /> Вес КАМАЗа</span>
                  <span className="tabular-nums font-medium">{order.weigh_in_kg ? `${formatMoney(order.weigh_in_kg)} кг` : "—"}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5 text-[var(--muted-foreground)]"><Package className="size-4" /> Мешков {order.status === "shipped" ? "(камера)" : "(заказано)"}</span>
                  <span className="tabular-nums font-medium">{order.status === "shipped" ? counted : ordered}</span>
                </div>
                {Number(order.bag_weight_kg ?? 0) > 0 && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-[var(--muted-foreground)]"><Scale className="size-4" /> Вес фасовки</span>
                    <span className="tabular-nums font-medium">{order.bag_weight_kg} кг</span>
                  </div>
                )}
                <div className="flex items-center justify-between border-t pt-3 text-sm">
                  <span className="flex items-center gap-1.5 font-medium"><Boxes className="size-4 text-[var(--ring)]" /> Вес груза (нетто)</span>
                  <span className="tabular-nums text-base font-bold text-[var(--ring)]">
                    {formatMoney(String(order.status === "shipped" ? Number(order.bag_estimate_kg ?? itemsWeight) : itemsWeight))} кг
                  </span>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Ошибки/уведомления всегда видны, не прячем под свёрнутую карточку */}
          {error && (
            <p className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
              {error}
            </p>
          )}

          {/* Подтверждение с ценами — оператор назначает цену каждой позиции для клиента */}
          {isManager && isNew && order.items.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Подтверждение · цены</CardTitle></CardHeader>
              <CardContent className="flex flex-col gap-3">
                <p className="text-xs text-[var(--muted-foreground)]">
                  Укажите цену за мешок для этого клиента. Цена запомнится для будущих заказов.
                </p>
                {order.items.map((it) => {
                  const id = it.id!;
                  const qty = Number(it.quantity);
                  const price = Number(prices[id] || 0);
                  return (
                    <div key={id} className="flex flex-col gap-1 border-t pt-2 first:border-0 first:pt-0">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">{it.product_label || `Товар #${it.product}`}</span>
                        <span className="text-xs text-[var(--muted-foreground)]">{qty} меш.</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Input type="number" min="0" placeholder="Цена за мешок"
                          value={prices[id] ?? ""}
                          onChange={(e) => setPrices((p) => ({ ...p, [id]: e.target.value }))} />
                        <span className="w-28 shrink-0 text-right text-sm tabular-nums">
                          {price > 0 ? `${formatMoney(String(price * qty))} ₸` : "—"}
                        </span>
                      </div>
                      {it.client_price != null && it.unit_price == null && (
                        <span className="text-[11px] text-[var(--muted-foreground)]">
                          Текущая цена клиента: {formatMoney(it.client_price)} ₸
                        </span>
                      )}
                    </div>
                  );
                })}
                <div className="flex items-center justify-between border-t pt-3">
                  <span className="text-sm text-[var(--muted-foreground)]">Итого</span>
                  <span className="text-lg font-bold tabular-nums">
                    {formatMoney(String(order.items.reduce((s, it) => s + Number(prices[it.id!] || 0) * Number(it.quantity), 0)))} ₸
                  </span>
                </div>
                <Button disabled={busy || order.items.some((it) => !(Number(prices[it.id!]) > 0))}
                  onClick={() => act(() => api.post(`/orders/${order.id}/confirm/`, {
                    prices: Object.fromEntries(order.items.map((it) => [it.id, prices[it.id!]])),
                  }))}>
                  Подтвердить заказ
                </Button>
                {order.status === "pending" && (
                  <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => act(() => api.post(`/orders/${order.id}/reject/`))}>
                    Отклонить
                  </Button>
                )}
              </CardContent>
            </Card>
          )}

          {/* «Действия» — скрыта, если в текущем состоянии действий нет; иначе сворачиваемая (раскрыта) */}
          {hasActions && (
            <CollapsibleCard title="Действия" defaultOpen>
              {isManager && isNew && order.items.length === 0 && (
                <Button variant="outline" disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${order.id}/confirm/`))}>
                  Подтвердить заказ
                </Button>
              )}
              {order.status === "shipped" && order.payment_status !== "settled" && (
                <>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-[var(--muted-foreground)]">Остаток долга</span>
                    <span className="tabular-nums font-semibold text-[var(--destructive)]">
                      {formatMoney(String(remaining))} ₸
                    </span>
                  </div>
                  <Link href={`/debts/clients/${order.client}`}>
                    <Button size="sm" className="w-full">Перейти к оплате долга</Button>
                  </Link>
                </>
              )}
              {order.status === "shipped" && order.payment_status === "settled" && (
                <p className="text-sm text-[var(--success)]">Заказ полностью оплачен.</p>
              )}
              {!isManager && isNew && (
                <p className="text-sm text-[var(--muted-foreground)]">Ожидает подтверждения менеджером.</p>
              )}
            </CollapsibleCard>
          )}

          {/* Ожидающие запросы на ручную смену — одобряет держатель orders.edit; раскрыты по умолчанию */}
          {canEditStatus && pendingReqs.length > 0 && (
            <CollapsibleCard
              title="Запросы на смену статуса"
              defaultOpen
              badge={<Badge tone="warning">{pendingReqs.length}</Badge>}
            >
              {pendingReqs.map((req) => (
                <div key={req.id} className="flex flex-col gap-2 rounded-lg border p-3">
                  <div className="text-sm">
                    <span className="text-[var(--muted-foreground)]">{req.requested_by_name || "Оператор"} → </span>
                    <span className="font-medium">{ORDER_STATUS_LABELS[req.to_status] ?? req.to_status}</span>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" disabled={busy}
                      onClick={() => act(() => api.post(`/orders/${order.id}/status-requests/${req.id}/approve/`))}>
                      Одобрить
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busy}
                      onClick={() => act(() => api.post(`/orders/${order.id}/status-requests/${req.id}/reject/`))}>
                      Отклонить
                    </Button>
                  </div>
                </div>
              ))}
            </CollapsibleCard>
          )}

          {/* «Сменить статус» — сворачиваемая, по умолчанию свёрнута */}
          {canViewStatus && (
            <CollapsibleCard title="Сменить статус">
              <p className="text-xs text-[var(--muted-foreground)]">
                {canEditStatus
                  ? "Ручная смена статуса для исправления ошибок."
                  : "Ручная смена требует одобрения главного оператора — будет создан запрос."}
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
                disabled={busy || (newStatus ? newStatus === order.status : true)}
                onClick={() => act(async () => {
                  const r = await api.post<{ applied: boolean }>(`/orders/${order.id}/set-status/`, { status: newStatus });
                  setNewStatus("");
                  if (r.data?.applied === false) {
                    setError("Запрос на смену статуса отправлен на одобрение.");
                  }
                })}>
                {canEditStatus ? "Применить" : "Запросить смену"}
              </Button>
            </CollapsibleCard>
          )}
        </div>
      </div>
    </AppShell>
  );
}

export default function OrderDetailPage(props: { params: Promise<{ id: string }> }) {
  return <RequirePerm perm="orders.view" title="Заказ"><OrderDetailPageInner {...props} /></RequirePerm>;
}
