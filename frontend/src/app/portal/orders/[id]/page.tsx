"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { PortalPaymentCard, type PortalPayMethod } from "@/components/portal/portal-payment-terminal";
import type { PortalPaymentPart } from "@/components/portal/portal-payment-parts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate } from "@/components/ui/data-state";
import { FileText } from "lucide-react";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { apiError } from "@/lib/api";
import { formatPortalMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { clientStep, downloadReceipt, payOrder, releasePortalPayment, setTruck } from "@/lib/portal-actions";
import type { PortalOrder } from "@/lib/types";

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, error: loadError, reload } = useApi<PortalOrder>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [releasePart, setReleasePart] = useState<PortalPaymentPart | null>(null);
  const [releaseError, setReleaseError] = useState("");

  // Payment webhooks and warehouse actions happen outside this page. Keep
  // the visible order current, including later refunds of a settled payment.
  useVisiblePolling(reload, 5_000, !busy);

  async function run(fn: () => Promise<unknown>) {
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

  async function pay(method: PortalPayMethod, amount: string, phone?: string) {
    await run(() => payOrder(Number(id), method, { amount, phone_number: method === "invoice" ? phone : undefined }));
  }

  async function confirmRelease() {
    if (!order || !releasePart) return;
    setBusy(true);
    setReleaseError("");
    try {
      await releasePortalPayment(order.id, releasePart.id);
      setReleasePart(null);
      await reload();
    } catch (e) {
      setReleaseError(apiError(e));
    } finally {
      setBusy(false);
    }
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
              {order.status === "shipped" && order.payment_status && (
                <Badge tone={PAYMENT_STATUS_TONE[order.payment_status] ?? "muted"} dot>
                  {PAYMENT_STATUS_LABELS[order.payment_status] ?? order.payment_status}
                </Badge>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-[var(--muted-foreground)]">
              Отдел продаж: {order.department_name || order.department || "Нет отдела"}
            </p>
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
              <CardTitle>{order.transport_type === "train" ? "Номер вагона" : "Отправка КАМАЗа"}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-[var(--muted-foreground)]">
                Укажите {order.transport_type === "train" ? "номер вагона из 8 цифр" : "номер КАМАЗа"} для документов и
                оператора. AI-подсчёт на моноблоке привязывается к самому заказу и выбранной камере.
              </p>
              {order.truck_number && (
                <p className="text-sm">
                  Текущий номер: <b>{order.truck_number}</b>
                </p>
              )}
              <div className="flex gap-2">
                <Input
                  placeholder={order.transport_type === "train" ? "Номер вагона · 8 цифр" : "Номер КАМАЗа"}
                  aria-label={order.transport_type === "train" ? "Номер вагона" : "Номер КАМАЗа"}
                  inputMode={order.transport_type === "train" ? "numeric" : undefined}
                  maxLength={order.transport_type === "train" ? 8 : undefined}
                  value={truck}
                  onChange={(e) => setTruckVal(e.target.value)}
                />
                <Button
                  disabled={busy || !truck || (order.transport_type === "train" && !/^[0-9]{8}$/.test(truck))}
                  onClick={() => run(() => setTruck(order.id, truck))}
                >
                  Сохранить
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {(step === "shipping" || step === "done") && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-6 text-center text-sm text-[var(--muted-foreground)]">
              {order.truck_number && (
                <p>
                  {order.transport_type === "train" ? "Вагон" : "КАМАЗ"}: <b>{order.truck_number}</b>
                </p>
              )}
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
