"use client";
import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button, buttonVariants } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import Link from "next/link";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import { currencySymbol, formatDateTime, formatIsoDate, formatMoney } from "@/lib/utils";
import {
  ORDER_MANUAL_STATUSES,
  ORDER_STATUS_LABELS,
  PORTAL_PAYMENT_METHOD_LABELS,
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONE,
  orderStatusGroup,
  translateOrderStatusMessage,
} from "@/lib/constants";
import { DataGate } from "@/components/ui/data-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ActionMenu } from "@/components/ui/action-menu";
import { PaymentChain, AddPaymentActions, PaidMethodBreakdown, paymentOpen } from "@/components/payment-chain";
import { OrderForm } from "@/components/order-form";
import { OrderPriceCorrectionModal } from "@/components/order-price-correction-modal";
import { Modal } from "@/components/ui/modal";
import { ShipmentRollbackModal } from "@/components/shipment-rollback-modal";
import {
  ArrowLeft,
  Archive,
  CalendarDays,
  CircleHelp,
  CircleDollarSign,
  Pencil,
  CopyPlus,
  Printer,
  Truck,
  UserRound,
} from "lucide-react";
import type { Client, EventLogPage, Order, Store } from "@/lib/types";

/** Строгая строка «подпись — значение»: единый табличный вид реквизитов. */
function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border)]/60 py-2 last:border-0">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

const EVENT_LABELS: Record<string, string> = {
  status: "Статус изменён",
  status_override: "Статус заказа",
  status_request: "Запрос статуса",
  payment: "Оплата",
  receipt: "Заказ принят",
  arrival: "Транспорт прибыл",
  loading: "Погрузка",
  shipment: "Заказ отгружен",
  shipment_rollback: "Откат отгрузки",
  order_repeat: "Повтор заказа",
  order_price_correction: "Стоимость скорректирована",
  debt_override: "Долг подтверждён",
};

function OrderDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { me } = useAuth();
  const canViewClients = can(me, "clients.view");
  const canViewReports = can(me, "reports.view");
  const { data: order, loading, error: loadError, reload } = useApi<Order>(`/orders/${id}/`);
  const { data: client } = useApi<Client>(order && canViewClients ? `/clients/${order.client}/` : null);
  const { data: store } = useApi<Store>(order?.store && canViewClients ? `/stores/${order.store}/` : null);
  const { data: events } = useApi<EventLogPage>(
    order && can(me, "events.view") ? `/events/?order=${order.id}&page_size=5` : null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [priceCorrectionOpen, setPriceCorrectionOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState("");

  async function confirmDelete() {
    setDelBusy(true);
    setDelError("");
    try {
      await api.delete(`/orders/${id}/`);
      router.push("/orders");
    } catch (e) {
      setDelError(apiError(e));
      setDelBusy(false);
    }
  }

  const isManager = can(me, "orders.confirm");
  const canEditStatus = can(me, "orders.edit");
  const canCorrectPrice = can(me, "orders.correct_price");
  const canRollback = can(me, "shipping.rollback");
  const canViewStatus = can(me, "orders.view");
  const [newStatus, setNewStatus] = useState("");
  const [rollbackOpen, setRollbackOpen] = useState(false);
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
    setBusy(true);
    setError("");
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!order)
    return (
      <AppShell title="Заказ">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const total = Number(order.total_amount);
  const paid = Number(order.paid_total);
  const remaining = total - paid;

  const hasRecordedWeight = order.weigh_in_kg != null;
  const itemsWeight = order.items.reduce((s, it) => s + Number(it.quantity) * Number(it.weight_kg ?? 0), 0);

  const isNew = order.status === "draft" || order.status === "pending";
  // Карточка редактирования доступна на любом этапе. Доменные блокировки
  // внутри формы/API защищают поля, которыми уже владеет погрузка или AI.
  const canEditOrder = canEditStatus;
  // Подтверждение с ценами рендерится отдельной карточкой (заказы с позициями).
  const confirmInPriceCard = isManager && isNew && order.items.length > 0;
  const pendingReqs = order.pending_status_requests ?? [];
  const pendingPayments = order.pending_payments ?? [];
  const hasPendingPayment = pendingPayments.length > 0;
  // Начать цепочку оплаты можно, пока есть непогашенный остаток.
  const canStartPayment = can(me, "payments.create") && paymentOpen(order) && remaining > 0 && !hasPendingPayment;
  const orderEvents = events?.results ?? [];
  // Ручной выбор ограничен четырьмя управляемыми статусами:
  // внутренние этапы ставят только бизнес-процессы, в журнале они видны как события.
  const currentStatusOption = orderStatusGroup(order.status);
  const statusOptions: readonly string[] =
    order.status === "shipped" && !canRollback
      ? ["shipped"]
      : currentStatusOption === "loaded"
        ? ["loaded", ...ORDER_MANUAL_STATUSES]
        : ORDER_MANUAL_STATUSES;
  const moneySymbol = currencySymbol(order.currency);

  return (
    <AppShell title="Заказы" section="Работа">
      <div className="mb-5 flex flex-wrap items-start gap-3 px-0.5 py-1">
        <Link
          href="/orders"
          aria-label="К списку заказов"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">Заказ #{order.id}</h2>
            <StatusBadge status={order.status} dot />
            {order.status === "shipped" && order.payment_status && (
              <Badge tone={PAYMENT_STATUS_TONE[order.payment_status] ?? "muted"} dot>
                {PAYMENT_STATUS_LABELS[order.payment_status] ?? order.payment_status}
              </Badge>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[var(--muted-foreground)]">
            <span className="flex items-center gap-1.5">
              <UserRound className="size-3.5" /> {order.client_name || "Клиент не указан"}
            </span>
            <span className="flex items-center gap-1.5">
              <CalendarDays className="size-3.5" /> {formatDateTime(order.created_at)}
            </span>
            <span className="flex items-center gap-1.5">
              <Truck className="size-3.5" />
              {order.transport_type === "train" ? "Вагон" : order.truck_number || "Машина не указана"}
            </span>
            {order.department_name && <span>{order.department_name}</span>}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 print:hidden">
          {/* ActionMenu вместо <details>: тот закрывался только повторным
              кликом по себе и оставался поверх страницы, перехватывая
              следующий тап — на телефоне он попадал в «В архив». */}
          <ActionMenu
            label="Действия"
            items={[
              { key: "guide", label: "Как работать", icon: CircleHelp, onSelect: () => setGuideOpen(true) },
              ...(can(me, "orders.create")
                ? [
                    {
                      key: "template",
                      label: "Использовать как шаблон",
                      icon: CopyPlus,
                      onSelect: () => router.push(`/orders?template=${order.id}`),
                    },
                  ]
                : []),
              ...(canEditOrder
                ? [{ key: "edit", label: "Изменить заказ", icon: Pencil, onSelect: () => setEditOpen(true) }]
                : []),
              ...(canCorrectPrice
                ? [
                    {
                      key: "correct-price",
                      label: "Корректировать стоимость",
                      icon: CircleDollarSign,
                      onSelect: () => setPriceCorrectionOpen(true),
                    },
                  ]
                : []),
            ]}
          />
          <Button size="icon" variant="outline" aria-label="Распечатать" onClick={() => window.print()}>
            <Printer className="size-4" />
          </Button>
          {canEditStatus && (
            <ActionMenu
              items={[
                {
                  key: "archive",
                  label: "В архив",
                  icon: Archive,
                  tone: "destructive",
                  onSelect: () => {
                    setDelError("");
                    setDelOpen(true);
                  },
                },
              ]}
            />
          )}
        </div>
      </div>

      {error && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-4">
          {/* Одна денежная полоса вместо трёх карточек: сумма → оплачено → долг
              читаются как одно уравнение, страница не дробится на боксы. */}
          <Card className="flex flex-wrap items-center gap-x-10 gap-y-3 p-4">
            <div className="min-w-0">
              <div className="text-xs text-[var(--muted-foreground)]">Сумма заказа</div>
              <div className="mt-1 truncate text-lg font-semibold leading-none tabular-nums">
                {formatMoney(order.total_amount)} {moneySymbol}
              </div>
            </div>
            <div className="min-w-0">
              <div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
              <div className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--success)]">
                {formatMoney(order.paid_total)} {moneySymbol}
              </div>
              {/* Разбивка по способам живёт в блоке «Оплата» ниже. */}
            </div>
            <div className="min-w-0">
              <div className="text-xs text-[var(--muted-foreground)]">Долг клиента</div>
              <div
                className={cn(
                  "mt-1 truncate text-lg font-semibold leading-none tabular-nums",
                  remaining > 0 ? "text-[var(--destructive)]" : "text-[var(--muted-foreground)]",
                )}
              >
                {formatMoney(String(Math.max(0, remaining)))} {moneySymbol}
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between p-4 pb-2">
              <CardTitle>Состав и доставка</CardTitle>
              <span className="text-xs text-[var(--muted-foreground)]">
                {order.items.length} {order.items.length === 1 ? "позиция" : "поз."}
              </span>
            </CardHeader>
            <CardContent className="p-4 pt-2">
              <Table>
                <THead>
                  <TR>
                    <TH>Товар</TH>
                    <TH className="text-right">Кол-во (мешки)</TH>
                    <TH className="text-right">Цена за мешок</TH>
                    <TH className="text-right">Сумма</TH>
                  </TR>
                </THead>
                <TBody>
                  {order.items.map((it, i) => {
                    const price = Number(it.price ?? 0);
                    const sum = price * Number(it.quantity);
                    return (
                      <TR key={it.id ?? `new-${i}`}>
                        <TD>
                          <span className="font-medium">
                            {it.product_label || `Товар #${it.product}`}
                            {it.weight_kg && (
                              <span className="block text-xs font-normal text-[var(--muted-foreground)]">
                                {it.weight_kg} кг/мешок
                              </span>
                            )}
                          </span>
                        </TD>
                        <TD className="text-right tabular-nums">{it.quantity}</TD>
                        <TD className="text-right tabular-nums text-[var(--muted-foreground)]">
                          {price ? `${formatMoney(it.price!)} ${moneySymbol}` : "—"}
                        </TD>
                        <TD className="text-right tabular-nums font-medium">
                          {formatMoney(String(sum))} {moneySymbol}
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>

              {/* Доставка — строгие строки «подпись — значение» под позициями. */}
              <div className="mt-3 flex flex-col border-t text-sm">
                <InfoRow label="Дата прибытия">
                  {order.arrival_date ? formatIsoDate(order.arrival_date) : "Не указана"}
                </InfoRow>
                <InfoRow label="Способ">
                  {order.transport_type === "train" ? "Вагон" : order.truck_number || "Машина"}
                </InfoRow>
                <InfoRow label="Отдел">{order.department_name ?? order.department}</InfoRow>
                <InfoRow label="Склад">{store?.name || (order.store ? `Склад #${order.store}` : "Основной")}</InfoRow>
                {hasRecordedWeight && (
                  <>
                    <InfoRow label="Учётный вес машины">{formatMoney(order.weigh_in_kg!)} кг</InfoRow>
                    <InfoRow label="Вес груза">
                      {formatMoney(String(Number(order.bag_estimate_kg ?? itemsWeight)))} кг
                    </InfoRow>
                  </>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between p-4 pb-2">
              <CardTitle>Оплата</CardTitle>
              <Badge
                tone={
                  pendingPayments.length
                    ? "warning"
                    : (PAYMENT_STATUS_TONE[order.payment_status ?? "unpaid"] ?? "muted")
                }
                dot
              >
                {pendingPayments.length ? "На проверке" : PAYMENT_STATUS_LABELS[order.payment_status ?? "unpaid"]}
              </Badge>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-2">
              {order.payment_method && (
                <div className="text-sm">
                  <InfoRow label="Выбор клиента">
                    {PORTAL_PAYMENT_METHOD_LABELS[order.payment_method] ?? order.payment_method}
                  </InfoRow>
                </div>
              )}
              {pendingPayments.length > 0 && (
                <>
                  <PaymentChain order={order} me={me} onChanged={reload} />
                  <div className="border-t pt-3">
                    <AddPaymentActions order={order} me={me} onChanged={reload} mode="request" />
                  </div>
                </>
              )}
              {pendingPayments.length === 0 && canStartPayment && (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-xs text-[var(--muted-foreground)]">К оплате</div>
                    <div className="mt-1 text-lg font-semibold tabular-nums">
                      {formatMoney(String(remaining))} {moneySymbol}
                    </div>
                  </div>
                  <AddPaymentActions order={order} me={me} onChanged={reload} />
                </div>
              )}
              {pendingPayments.length === 0 && !canStartPayment && (
                <div className="flex flex-col gap-1 text-sm">
                  <InfoRow label="Подтверждено системой">
                    <span className="tabular-nums">
                      {formatMoney(order.paid_total)} {moneySymbol}
                    </span>
                  </InfoRow>
                  <PaidMethodBreakdown order={order} className="text-xs" />
                </div>
              )}
              {order.status === "shipped" && remaining > 0 && canViewReports && (
                <Link
                  href={`/accounting/debts/clients/${order.client}`}
                  className="w-fit text-xs font-medium text-[var(--ring)] hover:underline"
                >
                  Открыть долг клиента →
                </Link>
              )}
            </CardContent>
          </Card>

          {/* Подтверждение с индивидуальными ценами остаётся рабочим блоком для новых заказов. */}
          {isManager && isNew && order.items.length > 0 && (
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle>Подтвердить заказ</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 p-4 pt-2">
                {order.items.map((it) => {
                  const itemId = it.id!;
                  const qty = Number(it.quantity);
                  const price = Number(prices[itemId] || 0);
                  return (
                    <div
                      key={itemId}
                      className="grid gap-2 border-t pt-3 first:border-0 first:pt-0 sm:grid-cols-[1fr_180px_110px] sm:items-center"
                    >
                      <div>
                        <div className="text-sm font-medium">{it.product_label || `Товар #${it.product}`}</div>
                        <div className="text-xs text-[var(--muted-foreground)]">{qty} меш.</div>
                      </div>
                      <Input
                        type="number"
                        min="0"
                        placeholder="Цена за мешок"
                        value={prices[itemId] ?? ""}
                        onChange={(e) => setPrices((p) => ({ ...p, [itemId]: e.target.value }))}
                      />
                      <span className="text-right text-sm font-medium tabular-nums">
                        {price > 0 ? `${formatMoney(String(price * qty))} ${moneySymbol}` : "—"}
                      </span>
                    </div>
                  );
                })}
                <div className="flex justify-end gap-2 border-t pt-3">
                  {order.status === "pending" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => act(() => api.post(`/orders/${order.id}/reject/`))}
                    >
                      Отклонить
                    </Button>
                  )}
                  <Button
                    size="sm"
                    disabled={busy || order.items.some((it) => !(Number(prices[it.id!]) > 0))}
                    onClick={() =>
                      act(() =>
                        api.post(`/orders/${order.id}/confirm/`, {
                          prices: Object.fromEntries(order.items.map((it) => [it.id, prices[it.id!]])),
                        }),
                      )
                    }
                  >
                    Подтвердить заказ
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        <aside className="flex flex-col gap-4 self-start print:hidden">
          <Card>
            <CardHeader className="p-4 pb-3">
              <CardTitle>Управление заказом</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-0">
              <div className="flex items-center justify-between text-xs">
                <span className="text-[var(--muted-foreground)]">Текущий статус</span>
                <StatusBadge status={order.status} dot />
              </div>
              {canViewStatus && (
                <>
                  <div className="grid gap-1.5">
                    <Label htmlFor="order-status" className="text-xs">
                      Изменить статус
                    </Label>
                    <Select
                      id="order-status"
                      value={newStatus || currentStatusOption}
                      onChange={(e) => setNewStatus(e.target.value)}
                    >
                      {statusOptions.map((s) => (
                        <option key={s} value={s}>
                          {ORDER_STATUS_LABELS[s]}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <Button
                    size="sm"
                    disabled={busy || !newStatus || newStatus === currentStatusOption}
                    onClick={() => {
                      if (order.status === "shipped" && newStatus !== "shipped") {
                        setRollbackOpen(true);
                        return;
                      }
                      void act(async () => {
                        const response = await api.post<{ applied: boolean }>(`/orders/${order.id}/set-status/`, {
                          status: newStatus,
                        });
                        setNewStatus("");
                        if (response.data?.applied === false)
                          setError("Запрос на смену статуса отправлен на одобрение.");
                      });
                    }}
                  >
                    {canEditStatus ? "Сохранить статус" : "Запросить смену"}
                  </Button>
                </>
              )}
              {isManager && isNew && !confirmInPriceCard && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${order.id}/confirm/`))}
                >
                  Подтвердить заказ
                </Button>
              )}
              {!isManager && isNew && (
                <p className="text-xs text-[var(--muted-foreground)]">Ожидает подтверждения менеджером.</p>
              )}
              {pendingReqs.map((req) => (
                <div key={req.id} className="rounded-lg border p-3 text-xs">
                  <div>
                    <span className="text-[var(--muted-foreground)]">{req.requested_by_name || "Оператор"} → </span>
                    <b>{ORDER_STATUS_LABELS[req.to_status] ?? req.to_status}</b>
                  </div>
                  {canEditStatus && (
                    <div className="mt-2 flex gap-2">
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() => act(() => api.post(`/orders/${order.id}/status-requests/${req.id}/approve/`))}
                      >
                        Одобрить
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => act(() => api.post(`/orders/${order.id}/status-requests/${req.id}/reject/`))}
                      >
                        Отклонить
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between p-4 pb-3">
              <CardTitle>Клиент</CardTitle>
              {canViewClients && (
                <Link href={`/clients/${order.client}`} className={buttonVariants({ size: "sm", variant: "outline" })}>
                  Открыть
                </Link>
              )}
            </CardHeader>
            <CardContent className="grid gap-2 p-4 pt-0 text-xs">
              <div className="flex justify-between gap-3">
                <span className="text-[var(--muted-foreground)]">Клиент</span>
                <span className="text-right font-medium">{client?.name || order.client_name || "—"}</span>
              </div>
              {(client?.first_name || client?.last_name) && (
                <div className="flex justify-between gap-3">
                  <span className="text-[var(--muted-foreground)]">Контакт</span>
                  <span className="text-right">{[client.first_name, client.last_name].filter(Boolean).join(" ")}</span>
                </div>
              )}
              <div className="flex justify-between gap-3">
                <span className="text-[var(--muted-foreground)]">Телефон</span>
                <span className="text-right">{client?.phone || order.client_phone || "—"}</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="p-4 pb-3">
              <CardTitle>История заказа</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="relative space-y-3 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-[var(--border)]">
                {orderEvents.length > 0 ? (
                  orderEvents.map((event, index) => (
                    <div key={event.id} className="relative flex gap-3 text-xs">
                      <span
                        className={cn(
                          "relative z-10 mt-1 size-2.5 shrink-0 rounded-full ring-4 ring-[var(--card)]",
                          index === 0 ? "bg-[var(--success)]" : "bg-[var(--muted-foreground)]/45",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">
                          {EVENT_LABELS[event.event_type] ?? translateOrderStatusMessage(event.message, event.payload)}
                        </div>
                        {EVENT_LABELS[event.event_type] && (
                          <div
                            className="mt-0.5 text-[var(--muted-foreground)]"
                            title={translateOrderStatusMessage(event.message, event.payload)}
                          >
                            {translateOrderStatusMessage(event.message, event.payload)}
                          </div>
                        )}
                        <div className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">
                          {formatDateTime(event.created_at)}
                          {event.user_name ? ` · ${event.user_name}` : ""}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <>
                    <div className="relative flex gap-3 text-xs">
                      <span className="relative z-10 mt-1 size-2.5 rounded-full bg-[var(--success)] ring-4 ring-[var(--card)]" />
                      <div className="flex-1">
                        <div className="font-medium">Заказ создан</div>
                        <div className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">
                          {formatDateTime(order.created_at)}
                        </div>
                      </div>
                    </div>
                    <div className="relative flex gap-3 text-xs">
                      <span className="relative z-10 mt-1 size-2.5 rounded-full bg-[var(--ring)] ring-4 ring-[var(--card)]" />
                      <div className="font-medium">{ORDER_STATUS_LABELS[order.status] ?? order.status}</div>
                    </div>
                  </>
                )}
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>

      <Modal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        eyebrow={`Работа · Заказ #${order.id}`}
        title="Изменить заказ"
        description="Позиции, цены, машина и дата прибытия. Изменения фиксируются в журнале."
        className="max-w-2xl"
      >
        {editOpen && (
          <OrderForm
            editing={order}
            onCancel={() => setEditOpen(false)}
            onDone={() => {
              setEditOpen(false);
              reload();
            }}
          />
        )}
      </Modal>

      <OrderPriceCorrectionModal
        order={priceCorrectionOpen ? order : null}
        onClose={() => setPriceCorrectionOpen(false)}
        onDone={() => {
          setPriceCorrectionOpen(false);
          reload();
        }}
      />

      <Modal
        open={guideOpen}
        onClose={() => setGuideOpen(false)}
        eyebrow="Быстрая подсказка"
        title="Как работать с заказом"
        description="Три шага от проверки товара до завершения оплаты."
        className="max-w-2xl"
      >
        <div className="overflow-hidden rounded-xl border bg-[#f7f8fa]">
          <Image
            src="/order-workflow-guide.png"
            alt="Товар, погрузка и оплата заказа"
            width={1536}
            height={512}
            className="h-auto w-full"
            priority
          />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg bg-[var(--muted)]/50 px-2 py-3">
            <div className="text-xs text-[var(--muted-foreground)]">1</div>
            <div className="mt-0.5 text-sm font-medium">Товар</div>
          </div>
          <div className="rounded-lg bg-[var(--muted)]/50 px-2 py-3">
            <div className="text-xs text-[var(--muted-foreground)]">2</div>
            <div className="mt-0.5 text-sm font-medium">Погрузка</div>
          </div>
          <div className="rounded-lg bg-[var(--muted)]/50 px-2 py-3">
            <div className="text-xs text-[var(--muted-foreground)]">3</div>
            <div className="mt-0.5 text-sm font-medium">Оплата</div>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={delOpen}
        onClose={() => setDelOpen(false)}
        title="Переместить заказ в архив?"
        description={`Заказ #${order.id} (${order.client_name ?? "клиент"}) исчезнет из рабочих списков и отчётов. Его можно будет восстановить через значок архива на странице заказов.`}
        confirmLabel="В архив"
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
      <ShipmentRollbackModal
        order={rollbackOpen ? order : null}
        initialTarget={
          (newStatus && newStatus !== "shipped" ? newStatus : "confirmed") as "pending" | "confirmed" | "cancelled"
        }
        onClose={() => setRollbackOpen(false)}
        onChanged={async () => {
          setNewStatus("");
          await reload();
        }}
      />
    </AppShell>
  );
}

export default function OrderDetailPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm="orders.view" title="Заказ">
      <OrderDetailPageInner {...props} />
    </RequirePerm>
  );
}
