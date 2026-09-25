"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PortalPaymentCard, type PortalPayMethod } from "@/components/portal/portal-payment-terminal";
import type { PortalPaymentPart } from "@/components/portal/portal-payment-parts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { TransportNumberFields } from "@/components/ui/transport-number-fields";
import { WagonList } from "@/components/ui/wagon-list";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/status-badge";
import { OrderPaymentBadge } from "@/components/payments/order-payment-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate } from "@/components/ui/data-state";
import { FileText, Lock } from "lucide-react";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { blobApiError } from "@/lib/api";
import { formatPortalMoney } from "@/lib/utils";
import { clientStep, downloadReceipt, payOrder, releasePortalPayment, setTruck } from "@/lib/portal-actions";
import {
  EMPTY_TRANSPORT_PAIR,
  isValidWagonNumber,
  sameTransportPair,
  transportBody,
  transportNumberError,
  transportPairOf,
  type TransportPair,
} from "@/lib/plates";
import type { PortalOrder } from "@/lib/types";
import { orderTransportText } from "@/lib/wagons";

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, error: loadError, reload, setData } = useApi<PortalOrder>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // null — номер не правили: в полях сохранённая пара заказа.
  const [draft, setDraft] = useState<TransportPair | null>(null);
  const [releasePart, setReleasePart] = useState<PortalPaymentPart | null>(null);
  const [releaseError, setReleaseError] = useState("");

  // Payment webhooks and warehouse actions happen outside this page. Keep
  // the visible order current, including later refunds of a settled payment.
  useVisiblePolling(reload, 5_000, !busy);

  /** Действие заказа. Ответ-заказ применяем, а не перечитываем (экран опрашивается). */
  async function run(fn: () => Promise<PortalOrder | void>, onError: (message: string) => void = setError) {
    setBusy(true);
    onError("");
    try {
      const next = await fn();
      if (next) setData(next);
    } catch (e) {
      // run() качает и чек (blob): текст ошибки сервера лежит внутри Blob.
      onError(await blobApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function pay(method: PortalPayMethod, amount: string, phone?: string) {
    await run(() => payOrder(Number(id), method, { amount, phone_number: method === "invoice" ? phone : undefined }));
  }

  const saved = order ? transportPairOf(order) : EMPTY_TRANSPORT_PAIR;
  const numbers = draft ?? saved;
  const train = order?.transport_type === "train";
  const wagonInvalid = train && !isValidWagonNumber(numbers.truck_number);

  async function saveTransport() {
    if (!order) return;
    await run(async () => {
      const next = await setTruck(order.id, transportBody(order.transport_type, numbers));
      setDraft(null);
      return next;
    });
  }

  async function confirmRelease() {
    if (!order || !releasePart) return;
    await run(async () => {
      const next = await releasePortalPayment(order.id, releasePart.id);
      setReleasePart(null);
      return next;
    }, setReleaseError);
  }

  if (!order)
    return (
      <AppShell title="Заказ" portal>
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const step = clientStep(order.status, order.payment_status, order.has_pending_payment);
  const releaseDescription =
    order.payment_status === "settled"
      ? releasePart?.method === "kaspi"
        ? "Заказ уже оплачен. Уберите лишний QR из заказа и не оплачивайте открытую ранее ссылку — она может действовать до истечения срока."
        : releasePart?.method === "invoice"
          ? "Заказ уже оплачен. Мы запросим отмену лишнего счёта и дождёмся подтверждения ApiPay."
          : "Заказ уже оплачен. Лишняя заявка на наличную оплату будет закрыта."
      : releasePart?.method === "kaspi"
        ? "После смены способа QR исчезнет из заказа, но уже открытая ссылка может оставаться доступной до истечения срока. Не оплачивайте старый QR."
        : releasePart?.method === "invoice"
          ? "Мы запросим отмену отправленного счёта. После подтверждения можно будет выбрать другой способ оплаты."
          : "Текущая заявка на наличную оплату будет отменена. После этого можно выбрать другой способ.";

  return (
    <AppShell title={`Заказ #${order.id}`} portal>
      <div className="flex flex-col gap-4 max-w-2xl">
        {loadError && (
          <div role="alert" className="rounded-lg border border-[var(--destructive)] p-3 text-sm">
            <p>Не удалось обновить заказ: {loadError} Показаны последние полученные данные.</p>
            <Button variant="outline" size="sm" onClick={() => void reload()} className="mt-2">
              Обновить
            </Button>
          </div>
        )}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Заказ #{order.id}</CardTitle>
            <div className="flex items-center gap-2">
              <StatusBadge status={order.status} />
              <OrderPaymentBadge order={order} dot />
            </div>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-[var(--muted-foreground)]">Отдел продаж: {order.department_name}</p>
            <Table>
              <THead>
                <TR>
                  <TH>Товар</TH>
                  <TH>Мешков</TH>
                </TR>
              </THead>
              <TBody>
                {order.items.map((it) => (
                  <TR key={it.id}>
                    <TD>{it.product_label}</TD>
                    <TD>{it.quantity}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {/* Вагоны отгрузки по отчёту — клиенту только для чтения. */}
            {order.wagons && order.wagons.length > 0 && (
              <WagonList wagons={order.wagons} station={order.rail_station} className="mt-4" />
            )}
            <div className="mt-4 flex justify-between border-t pt-3 text-sm">
              <span className="text-[var(--muted-foreground)]">Итого</span>
              <span
                className={
                  order.total_amount == null ? "font-medium text-[var(--muted-foreground)]" : "font-bold tabular-nums"
                }
              >
                {formatPortalMoney(order.total_amount, order.currency)}
              </span>
            </div>
          </CardContent>
        </Card>

        {error && step !== "pay" && <p className="text-sm text-[var(--destructive)]">{error}</p>}

        {step === "pending" && (
          <Card>
            <CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              Заказ на рассмотрении. Ожидайте решения.
            </CardContent>
          </Card>
        )}

        {step === "rejected" && (
          <Card>
            <CardContent className="space-y-2 py-6 text-sm">
              <p className="font-medium text-[var(--destructive)]">
                {order.status === "rejected" ? "Заявка отклонена" : "Заказ отменён"}
              </p>
              {order.status === "rejected" && <p>{order.rejection_reason || "Уточните причину у менеджера."}</p>}
            </CardContent>
          </Card>
        )}

        {step === "pay" && (
          <PortalPaymentCard
            order={order}
            busy={busy}
            error={error}
            onPay={pay}
            onRelease={(part) => {
              setReleaseError("");
              setReleasePart(part);
            }}
          />
        )}

        {step === "truck" && (
          <Card>
            <CardHeader>
              <CardTitle>{train ? "Номер вагона" : "Номер машины"}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {order.transport_locked ? (
                <p className="flex flex-wrap items-center gap-1.5 text-sm">
                  <Lock className="size-4 text-[var(--muted-foreground)]" /> Номер указал менеджер:{" "}
                  <b className="tabular-nums">{orderTransportText(order)}</b>
                </p>
              ) : (
                <>
                  <p className="text-sm text-[var(--muted-foreground)]">
                    Можно указать {train ? "номер вагона из 8 цифр" : "номер машины и прицепа"} заранее — тогда он
                    попадёт в документы. Если номера пока нет, ничего вводить не нужно: оператор укажет его при
                    отгрузке.
                  </p>
                  <TransportNumberFields
                    id="portal"
                    transportType={order.transport_type}
                    defaultCountry={order.client_country}
                    value={numbers}
                    errors={train ? { truck: transportNumberError(numbers.truck_number, "train") } : undefined}
                    onChange={setDraft}
                  />
                  <Button
                    className="self-start"
                    disabled={busy || !numbers.truck_number.trim() || wagonInvalid || sameTransportPair(numbers, saved)}
                    onClick={() => void saveTransport()}
                  >
                    Сохранить
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        )}

        {/* Машина уже на территории (заехала, грузится, отгружена): номер клиенту не
            показываем совсем — решение владельца; сервер его и не отдаёт. */}
        {(step === "shipping" || step === "done") && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-6 text-center text-sm text-[var(--muted-foreground)]">
              <p>{step === "done" ? "Заказ отгружен и оплачен." : "Заказ в обработке на складе."}</p>
              {step === "done" && order.receipt_available && (
                <Button variant="outline" disabled={busy} onClick={() => run(() => downloadReceipt(order.id))}>
                  <FileText className="size-4" /> Скачать квитанцию
                </Button>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      <ConfirmDialog
        open={releasePart != null}
        onClose={() => {
          setReleasePart(null);
          setReleaseError("");
        }}
        title={order.payment_status === "settled" ? "Закрыть лишнюю заявку?" : "Выбрать другой способ оплаты?"}
        description={releaseDescription}
        confirmLabel={order.payment_status === "settled" ? "Закрыть заявку" : "Да, сменить способ"}
        confirmVariant="default"
        busy={busy}
        error={releaseError}
        onConfirm={() => void confirmRelease()}
      />
    </AppShell>
  );
}
