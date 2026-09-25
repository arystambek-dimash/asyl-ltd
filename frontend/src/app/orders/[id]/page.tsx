"use client";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import { EventTimeline, EventTimelineItem } from "@/components/ui/event-timeline";
import { LoadMore } from "@/components/ui/load-more";
import { usePagedApi } from "@/lib/use-paged-api";
import { Button, buttonVariants } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { InfoRow } from "@/components/ui/info-row";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import Link from "next/link";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { safeBackPath } from "@/lib/navigation";
import { UNPRICED_TOTAL, hasUnpricedItems, isChangedRequestError } from "@/lib/orders";
import { remainingOf } from "@/lib/debt-orders";
import { cn, formatCurrency, formatDateTime, formatIsoDate, formatMoney } from "@/lib/utils";
import { orderTransportLabel } from "@/lib/wagons";
import { ORDER_REVIEWABLE_STATUSES, orderStatusLabel, translateOrderStatusMessage } from "@/lib/constants";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { ActionMenu } from "@/components/ui/action-menu";
import { PaymentChain, paidByMethod } from "@/components/payment-chain";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { OrderPaymentBadge } from "@/components/payments/order-payment-badge";
import { OverpaymentRefundButton } from "@/components/payments/overpayment-refund";
import { useQrRefundWindow } from "@/components/transactions/qr-refund-modal";
import { payRetryFromParams, payRetryPageNotice, withoutPayRetry } from "@/components/orders/pay-now";
import { WagonList } from "@/components/ui/wagon-list";
import { Modal } from "@/components/ui/modal";
import { OrderStatusSelect } from "@/components/order-status-select";
import { useOrderStatusChange } from "@/components/orders/use-order-status-change";
import { useOrderActions } from "@/components/orders/order-actions";
import { OrderReviewDialogs } from "@/components/orders/order-review-dialogs";
import { ArrowLeft, CalendarDays, CircleHelp, CopyPlus, Printer, Truck, UserRound } from "lucide-react";
import { eventTypeMeta } from "@/lib/event-types";
import type { Client, EventLog, Order, Store } from "@/lib/types";

function OrderDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const back = safeBackPath(searchParams.get("back"));
  const { me } = useAuth();
  const canViewClients = can(me, "clients.view");
  const canViewReports = can(me, "reports.view");
  const { data: order, loading, error: loadError, reload } = useApi<Order>(`/orders/${id}/`);
  const { data: client } = useApi<Client>(order && canViewClients ? `/clients/${order.client}/` : null);
  // «Оплата сразу» из формы не прошла: карточка открывает «Принять оплату» с тем же способом и суммой.
  const payRetryKey = searchParams.toString();
  const payRetry = useMemo(() => payRetryFromParams(new URLSearchParams(payRetryKey)), [payRetryKey]);
  const clearPayRetry = useCallback(
    () => router.replace(`/orders/${id}${withoutPayRetry(new URLSearchParams(payRetryKey))}`),
    [id, payRetryKey, router],
  );
  const [section, setSection] = useState(payRetry ? "payment" : "items");
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    // The printed order includes all three sections, regardless of the open tab.
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  const { data: store } = useApi<Store>(
    order?.store && can(me, "stores.view") && section === "delivery" ? `/stores/${order.store}/` : null,
  );
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const [rejectionOpen, setRejectionOpen] = useState(false);
  const events = usePagedApi<EventLog>(
    order && section === "history" && can(me, "events.view") ? `/events/?order=${order.id}` : null,
    20,
  );
  // Возврат переплаты по Kaspi QR: окно ссылки переживает исчезновение баннера переплаты.
  const qrRefund = useQrRefundWindow(() => Promise.all([reload(), events.reload()]));
  const mutationInFlight = useRef(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const orderActions = useOrderActions({
    onChanged: () => Promise.all([reload(), events.reload()]),
    onArchived: () => router.push("/orders"),
  });

  const isManager = can(me, "orders.confirm");
  const canEditStatus = can(me, "orders.edit");
  const canRollback = can(me, "orders.rollback");

  // Заказ не подтвердился при создании или ответ об оплате потерялся — окно
  // оплаты само не открывается, объясняем на странице.
  const payRetryNotice = payRetry && order ? payRetryPageNotice(payRetry, order) : "";
  useEffect(() => {
    if (!payRetryNotice) return;
    setError(payRetryNotice);
    clearPayRetry();
  }, [payRetryNotice, clearPayRetry]);
  const payRetryDialog = payRetry && !payRetryNotice ? payRetry : null;

  async function act(fn: () => Promise<unknown>) {
    if (mutationInFlight.current) return false;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
      await Promise.all([reload(), events.reload()]);
      return true;
    } catch (e) {
      setError(apiError(e));
      return false;
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  // Окно подтверждения показывает ошибку страницы как свою: открываем его с чистой.
  function openConfirmation() {
    setError("");
    setConfirmationOpen(true);
  }

  const statusChange = useOrderStatusChange({
    canEdit: canEditStatus,
    canRollback,
    onConfirm: openConfirmation,
    onChanged: () => Promise.all([reload(), events.reload()]),
  });

  // «Ожидает загрузки» у заявки в списке ведёт сюда с ?confirm=1: окно подтверждения открывается сразу.
  const confirmRequested = searchParams.get("confirm") === "1";
  useEffect(() => {
    // Права ещё не пришли — не решаем без них, иначе признак сотрётся впустую.
    if (!confirmRequested || !order || !me) return;
    if (isManager && ORDER_REVIEWABLE_STATUSES.includes(order.status)) {
      setError("");
      setConfirmationOpen(true);
    }
    const rest = new URLSearchParams(searchParams.toString());
    rest.delete("confirm");
    const query = rest.toString();
    router.replace(`/orders/${id}${query ? `?${query}` : ""}`);
  }, [confirmRequested, isManager, me, order, searchParams, router, id]);

  if (!order)
    return (
      <AppShell title="Заказ">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const unpriced = hasUnpricedItems(order.items);
  const remaining = remainingOf(order);

  const hasRecordedWeight = order.weigh_in_kg != null;

  const isNew = order.status === "draft" || order.status === "pending";
  const canReview = isManager && isNew;
  const pendingReqs = order.pending_status_requests ?? [];
  const pendingPayments = order.pending_payments ?? [];
  const hasPendingPayment = pendingPayments.length > 0;
  // Начать цепочку оплаты можно, пока есть непогашенный остаток. Когда деньги
  // принимаются (предоплата до отгрузки или оплата после), решает сервер.
  const canStartPayment =
    can(me, "payments.create") && Boolean(order.payment_open) && remaining > 0 && !hasPendingPayment;
  const overpaid = Number(order.overpaid_amount ?? 0);
  const orderEvents = events.items;

  return (
    <AppShell title="Заказы" section="Работа">
      <div className="mb-5 flex flex-wrap items-start gap-3 px-0.5 py-1">
        <Link
          href={back ?? "/orders"}
          aria-label={back ? "Назад" : "К списку заказов"}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">Заказ #{order.id}</h2>
            <StatusBadge status={order.status} dot />
            <OrderPaymentBadge order={order} pending={hasPendingPayment} dot />
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
              {orderTransportLabel(order, "Машина не указана")}
            </span>
            <span>{order.department_name}</span>
          </div>
        </div>
        <div className="flex w-full shrink-0 items-center justify-end gap-2 sm:w-auto sm:justify-start print:hidden">
          {/* ActionMenu вместо <details>: тот закрывался только повторным
              кликом по себе и оставался поверх страницы, перехватывая
              следующий тап — на телефоне он попадал в «В архив». */}
          <ActionMenu
            label="Действия"
            triggerText="Действия"
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
              ...orderActions.items(order),
            ]}
          />
          <Button size="icon" variant="outline" aria-label="Распечатать" onClick={() => window.print()}>
            <Printer className="size-4" />
          </Button>
        </div>
      </div>

      {error && <ErrorAlert message={error} className="mb-4" />}

      <Card className="mb-4 flex flex-wrap items-center gap-x-10 gap-y-3 p-4">
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Сумма заказа</div>
          <div className="mt-1 truncate text-lg font-semibold leading-none tabular-nums">
            {unpriced ? UNPRICED_TOTAL : formatCurrency(order.total_amount, order.currency)}
          </div>
        </div>
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
          <div className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--success)]">
            {formatCurrency(order.paid_total, order.currency)}
          </div>
          {/* Разбивка по способам живёт в блоке «Оплата» ниже. */}
        </div>
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted-foreground)]">Осталось оплатить</div>
          <div
            className={cn(
              "mt-1 truncate text-lg font-semibold leading-none tabular-nums",
              remaining > 0 ? "text-[var(--destructive)]" : "text-[var(--muted-foreground)]",
            )}
          >
            {["rejected", "cancelled"].includes(order.status)
              ? "—"
              : unpriced
                ? "После расчёта"
                : isNew
                  ? "После подтверждения"
                  : formatCurrency(Math.max(0, remaining), order.currency)}
          </div>
        </div>
      </Card>
      {overpaid > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-4 py-3 text-sm print:hidden">
          <span>
            Переплата <b className="tabular-nums">{formatCurrency(order.overpaid_amount!, order.currency)}</b> — вернуть
            клиенту
          </span>
          <OverpaymentRefundButton
            order={order}
            me={me}
            onChanged={() => Promise.all([reload(), events.reload()])}
            onQrRefund={qrRefund.start}
          />
        </div>
      )}
      {qrRefund.modal}
      {canReview && (
        <Card className="mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <h3 className="font-semibold">Заявка ждёт решения</h3>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">Проверьте отдел продаж и цены.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={openConfirmation} disabled={busy}>
              Проверить и подтвердить
            </Button>
            {order.status === "pending" && (
              <Button variant="outline" onClick={() => setRejectionOpen(true)} disabled={busy}>
                Отклонить
              </Button>
            )}
          </div>
        </Card>
      )}
      {order.status === "rejected" && (
        <div className="mb-4 rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-4 text-sm">
          <p className="font-medium">Причина отклонения</p>
          <p className="mt-1">{order.rejection_reason || "Причина не указана"}</p>
        </div>
      )}
      <Tabs
        className="mb-4 gap-3 overflow-x-auto sm:gap-6 print:hidden"
        label="Разделы заказа"
        active={section}
        onChange={setSection}
        tabs={[
          { key: "items", label: "Состав", count: order.items.length },
          { key: "payment", label: "Оплата", ...(pendingPayments.length ? { count: pendingPayments.length } : {}) },
          { key: "delivery", label: "Доставка" },
          ...(can(me, "events.view") ? [{ key: "history", label: "История" }] : []),
        ]}
      />
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div
          className="min-w-0 space-y-4"
          role="tabpanel"
          aria-label={{ items: "Состав", payment: "Оплата", delivery: "Доставка", history: "История" }[section]}
        >
          {(section === "items" || printing) && (
            <Card>
              <CardHeader className="flex-row items-center justify-between p-4 pb-2">
                <CardTitle>Состав заказа</CardTitle>
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
                      const price = Number(it.unit_price ?? 0);
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
                            {price ? formatCurrency(it.unit_price!, order.currency) : "—"}
                          </TD>
                          <TD className="text-right tabular-nums font-medium">
                            {it.unit_price == null ? "—" : formatCurrency(sum, order.currency)}
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
          )}
          {(section === "delivery" || printing) && (
            <Card>
              <CardHeader>
                <CardTitle>Доставка и отгрузка</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mt-3 flex flex-col border-t text-sm">
                  <InfoRow label="Дата прибытия">
                    {order.arrival_date ? formatIsoDate(order.arrival_date) : "Не указана"}
                  </InfoRow>
                  <InfoRow label="Способ">{orderTransportLabel(order, "Машина")}</InfoRow>
                  {order.transport_type === "train" && order.rail_station && (
                    <InfoRow label="Станция назначения">{order.rail_station}</InfoRow>
                  )}
                  <InfoRow label="Отдел">{order.department_name}</InfoRow>
                  <InfoRow label="Склад отгрузки">{order.warehouse_name}</InfoRow>
                  <InfoRow label="Магазин">
                    {store?.name || (order.store ? `Магазин #${order.store}` : "Без магазина")}
                  </InfoRow>
                  {hasRecordedWeight && (
                    <>
                      <InfoRow label="Учётный вес машины">{formatMoney(order.weigh_in_kg!)} кг</InfoRow>
                      <InfoRow label="Вес груза">{formatMoney(order.bag_estimate_kg)} кг</InfoRow>
                    </>
                  )}
                </div>
                {order.wagons && order.wagons.length > 0 && (
                  <WagonList wagons={order.wagons} station={order.rail_station} variant="table" className="mt-3" />
                )}
                {order.notes && (
                  <div className="mt-3 rounded-lg bg-[var(--muted)] p-3 text-sm">
                    <p className="mb-1 font-medium">Примечание</p>
                    <p className="whitespace-pre-wrap">{order.notes}</p>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
          {(section === "payment" || printing) && (
            <Card>
              <CardHeader className="flex-row items-center justify-between p-4 pb-2">
                <CardTitle>Оплата</CardTitle>
                <OrderPaymentBadge order={order} pending={hasPendingPayment} dot />
              </CardHeader>
              <CardContent className="flex flex-col gap-3 p-4 pt-2">
                <p className="text-sm">
                  Отдел учёта: <b>{order.department_name}</b>
                </p>
                {order.payment_method && order.payment_method !== "pending" && (
                  <div className="text-sm">
                    <InfoRow label="Выбор клиента">{order.payment_method_label}</InfoRow>
                  </div>
                )}
                {pendingPayments.length > 0 && (
                  <>
                    <PaymentChain order={order} me={me} onChanged={reload} />
                    <OrderPaymentActions
                      order={order}
                      me={me}
                      onChanged={() => reload()}
                      className="border-t pt-3"
                      autoOpen={payRetryDialog}
                      onAutoOpened={clearPayRetry}
                    />
                  </>
                )}
                {pendingPayments.length === 0 && canStartPayment && (
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">К оплате</div>
                      <div className="mt-1 text-lg font-semibold tabular-nums">
                        {formatCurrency(remaining, order.currency)}
                      </div>
                    </div>
                    <OrderPaymentActions
                      order={order}
                      me={me}
                      onChanged={() => reload()}
                      autoOpen={payRetryDialog}
                      onAutoOpened={clearPayRetry}
                    />
                  </div>
                )}
                {pendingPayments.length === 0 && !canStartPayment && (
                  <div className="flex flex-col gap-1 text-sm">
                    {isNew && (
                      <p className="mb-2 text-[var(--muted-foreground)]">
                        Оплата станет доступна после подтверждения заказа.
                      </p>
                    )}
                    <InfoRow label="Получено">
                      <span className="tabular-nums">{formatCurrency(order.paid_total, order.currency)}</span>
                    </InfoRow>
                    <PaidMethodSummary parts={paidByMethod(order)} className="text-xs" />
                  </div>
                )}
                {order.is_debt && canViewReports && (
                  <Link
                    href={`/accounting/debts/clients/${order.client}`}
                    className="w-fit text-xs font-medium text-[var(--ring)] hover:underline"
                  >
                    Открыть долг клиента →
                  </Link>
                )}
              </CardContent>
            </Card>
          )}
          {section === "history" && (
            <Card>
              <CardHeader className="p-4 pb-3">
                <CardTitle>История заказа</CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                {(events.loading || events.error) && (
                  <DataGate loading={events.loading} error={events.error} onRetry={events.reload} />
                )}
                <EventTimeline>
                  {orderEvents.length > 0 ? (
                    orderEvents.map((event, index) => (
                      <EventTimelineItem
                        key={event.id}
                        title={translateOrderStatusMessage(event.message) || eventTypeMeta(event.event_type).label}
                        at={event.created_at}
                        userName={event.user_name}
                        highlighted={index === 0}
                      />
                    ))
                  ) : !events.loading && !events.error ? (
                    <>
                      <EventTimelineItem title="Заказ создан" at={order.created_at} highlighted />
                      <div className="relative flex gap-3 text-xs">
                        <span className="relative z-10 mt-1 size-2.5 rounded-full bg-[var(--ring)] ring-4 ring-[var(--card)]" />
                        <div className="font-medium">{orderStatusLabel(order.status)}</div>
                      </div>
                    </>
                  ) : null}
                </EventTimeline>
                <LoadMore
                  shown={events.items.length}
                  total={events.count}
                  hasMore={events.hasMore}
                  loading={events.loadingMore}
                  onClick={events.loadMore}
                />
              </CardContent>
            </Card>
          )}
        </div>
        <aside className="grid gap-4 print:hidden">
          <Card>
            <CardHeader className="flex-row items-center justify-between p-4 pb-3">
              <CardTitle>Клиент</CardTitle>
              {canViewClients && (
                <Link href={`/clients/${order.client}`} className={buttonVariants({ size: "sm", variant: "outline" })}>
                  Открыть
                </Link>
              )}
            </CardHeader>
            <CardContent className="flex flex-col p-4 pt-0 text-xs">
              <InfoRow label="Клиент">{client?.name || order.client_name || "—"}</InfoRow>
              {(client?.first_name || client?.last_name) &&
                [client.first_name, client.last_name].filter(Boolean).join(" ") !==
                  (client.name || order.client_name) && (
                  <InfoRow label="Контакт">{[client.first_name, client.last_name].filter(Boolean).join(" ")}</InfoRow>
                )}
              <InfoRow label="Телефон">{client?.phone || order.client_phone || "—"}</InfoRow>
            </CardContent>
          </Card>
          {!isNew && (
            <details className="rounded-xl border bg-[var(--card)]">
              <summary className="cursor-pointer p-4 text-sm font-medium">Изменить статус</summary>
              <Card>
                <CardHeader className="p-4 pb-3">
                  <CardTitle>Статус заказа</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-3 p-4 pt-0">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-[var(--muted-foreground)]">Текущий статус</span>
                    <StatusBadge status={order.status} dot />
                  </div>
                  {statusChange.canChoose(order) && (
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-[var(--muted-foreground)]">
                        {canEditStatus ? "Новый статус" : "Запросить смену"}
                      </span>
                      <OrderStatusSelect
                        status={order.status}
                        disabled={busy || statusChange.busyId === order.id}
                        onChange={(target) => statusChange.choose(order, target)}
                      />
                    </div>
                  )}
                  {statusChange.error && <p className="text-xs text-[var(--destructive)]">{statusChange.error}</p>}
                  {pendingReqs.map((req) => (
                    <div key={req.id} className="rounded-lg border p-3 text-xs">
                      <div>
                        <span className="text-[var(--muted-foreground)]">{req.requested_by_name || "Оператор"} → </span>
                        <b>{orderStatusLabel(req.to_status)}</b>
                      </div>
                      {canEditStatus && (
                        <div className="mt-2 flex gap-2">
                          <Button
                            size="sm"
                            disabled={busy}
                            onClick={() =>
                              act(() => api.post(`/orders/${order.id}/status-requests/${req.id}/approve/`))
                            }
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
            </details>
          )}
          {!isManager && isNew && <p className="text-sm text-[var(--muted-foreground)]">Ожидает решения менеджера.</p>}
        </aside>
      </div>
      <OrderReviewDialogs
        confirming={confirmationOpen && canReview ? order : null}
        rejecting={rejectionOpen ? order : null}
        busy={busy}
        error={error}
        onConfirm={(target, data) =>
          act(() =>
            api.post(`/orders/${target.id}/confirm/`, data).catch((cause: unknown) => {
              // Состав заявки изменился: окно перечитает заказ, ошибка останется в нём.
              if (isChangedRequestError(cause)) void reload();
              throw cause;
            }),
          )
        }
        onConfirmClose={() => setConfirmationOpen(false)}
        onRejectClose={() => setRejectionOpen(false)}
        onRejected={() => {
          void reload();
          void events.reload();
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

      {orderActions.dialogs}
      {statusChange.dialogs}
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
