"use client";
import { use, useEffect, useState } from "react";
import Image from "next/image";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ProgressBar } from "@/components/ui/progress-bar";
import { DataGate } from "@/components/ui/data-state";
import {
  AlertTriangle,
  ArrowLeft,
  Banknote,
  CheckCircle2,
  Clock,
  FileText,
  HandCoins,
  QrCode,
  Smartphone,
} from "lucide-react";
import { useApi } from "@/lib/use-api";
import { apiError } from "@/lib/api";
import { currencySymbol, formatDateTime, formatMoney, formatPortalMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { clientStep, downloadReceipt, payOrder, releasePortalPayment, setTruck } from "@/lib/portal-actions";
import type { PortalOrder, PortalPaymentMethod } from "@/lib/types";

type PortalPaymentPart = PortalOrder["payment_parts"][number];

const PAYABLE_PROVIDER_STATUSES = new Set(["creating", "processing", "pending"]);

const PROVIDER_STATUS_LABELS: Record<string, string> = {
  creating: "Создаётся",
  processing: "Ожидает оплаты",
  pending: "Ожидает оплаты",
  paid: "Оплачен",
  cancelling: "Отменяется",
  cancelled: "Отменён",
  expired: "Истёк",
  superseded: "Заменён",
  error: "Ошибка",
};

function providerStatusTone(status: string): "muted" | "success" | "warning" | "destructive" {
  if (status === "paid") return "success";
  if (PAYABLE_PROVIDER_STATUSES.has(status) || status === "cancelling") return "warning";
  if (status === "error") return "destructive";
  return "muted";
}

function paymentAmountError(value: string, available: number) {
  if (!value.trim()) return "Введите сумму оплаты.";
  const parsed = Number(value.trim().replace(",", "."));
  if (!Number.isFinite(parsed) || parsed <= 0) return "Сумма должна быть больше нуля.";
  if (Math.abs(parsed * 100 - Math.round(parsed * 100)) > 1e-7) {
    return "Укажите сумму с точностью не более двух знаков.";
  }
  if (Math.round(parsed * 100) > Math.round(available * 100)) {
    return `Доступно не более ${formatMoney(available)}.`;
  }
  return "";
}

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, error: loadError, reload } = useApi<PortalOrder>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [paymentMode, setPaymentMode] = useState<"qr" | "invoice" | null>(null);
  const [paymentPhone, setPaymentPhone] = useState("");
  const [paymentAmount, setPaymentAmount] = useState("");
  const [amountTouched, setAmountTouched] = useState(false);
  const [releasePart, setReleasePart] = useState<PortalPaymentPart | null>(null);
  const [releaseError, setReleaseError] = useState("");
  const [failedQrImages, setFailedQrImages] = useState<Set<number>>(() => new Set());

  const loadedOrderId = order?.id;
  const availableValue = order?.available_amount ?? order?.remaining_amount ?? "";
  const clientPhone = order?.client_phone ?? "";

  useEffect(() => {
    if (loadedOrderId == null) return;
    const nextAvailable = Number(availableValue);
    setPaymentAmount(Number.isFinite(nextAvailable) && nextAvailable > 0 ? String(availableValue) : "");
    setAmountTouched(false);
    setPaymentMode(null);
  }, [availableValue, loadedOrderId]);

  useEffect(() => {
    if (loadedOrderId == null) return;
    setPaymentPhone(clientPhone);
  }, [clientPhone, loadedOrderId]);

  async function run(fn: () => Promise<unknown>, resetPayment = false) {
    setBusy(true);
    setError("");
    try {
      await fn();
      if (resetPayment) {
        setPaymentMode(null);
        setPaymentAmount("");
        setAmountTouched(false);
      }
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function selectPayment(method: PortalPaymentMethod, amount?: string) {
    await payOrder(Number(id), method, { amount });
  }

  async function confirmRelease() {
    if (!order || !releasePart) return;
    setBusy(true);
    setReleaseError("");
    try {
      await releasePortalPayment(order.id, releasePart.id);
      setReleasePart(null);
      setPaymentMode(null);
      setPaymentAmount("");
      setAmountTouched(false);
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
  const remaining = order.remaining_amount == null ? 0 : Math.max(0, Number(order.remaining_amount));
  const phone = paymentPhone;
  const available = Number(order.available_amount ?? remaining);
  const chosenAmount = paymentAmount;
  const normalizedAmount = chosenAmount.trim().replace(",", ".");
  const amountError = paymentAmountError(chosenAmount, available);
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

        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}

        {step === "pending" && (
          <Card>
            <CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              Заказ на рассмотрении. Ожидайте решения.
            </CardContent>
          </Card>
        )}

        {step === "rejected" && (
          <Card>
            <CardContent className="py-6 text-center text-sm text-[var(--destructive)]">Заказ отклонён.</CardContent>
          </Card>
        )}

        {step === "pay" &&
          (() => {
            const total = Number(order.total_amount ?? 0);
            const paid = Number(order.paid_total ?? 0);
            const pct = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;
            const partial = paid > 0 && remaining > 0;
            return (
              <Card>
                <CardHeader>
                  <CardTitle>Оплата</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  {/* сводка */}
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">Сумма</div>
                      <div className="tabular-nums font-medium">
                        {formatMoney(order.total_amount ?? 0)} {currencySymbol(order.currency)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">Оплачено</div>
                      <div className="tabular-nums text-[var(--success)]">
                        {formatMoney(order.paid_total ?? "0")} {currencySymbol(order.currency)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">Остаток</div>
                      <div className="tabular-nums font-semibold text-[var(--destructive)]">
                        {formatMoney(remaining)} {currencySymbol(order.currency)}
                      </div>
                    </div>
                  </div>
                  <div>
                    <div className="mb-1.5 flex justify-between text-xs text-[var(--muted-foreground)]">
                      <span>{partial ? "Частично оплачено" : "Прогресс оплаты"}</span>
                      <span className="tabular-nums">{pct}%</span>
                    </div>
                    <ProgressBar pct={pct} />
                  </div>

                  {order.debt_requested ? (
                    <div className="flex items-start gap-2 rounded-lg border border-[var(--primary)]/25 bg-[var(--primary)]/5 p-3 text-sm">
                      <HandCoins className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
                      Запрос «В долг» отправлен. Ожидайте решения сотрудника.
                    </div>
                  ) : (
                    <>
                      {order.payment_parts.map((part) => {
                        const provider = part.apipay_invoice;
                        const providerIsPayable = provider != null && PAYABLE_PROVIDER_STATUSES.has(provider.status);
                        const qrIsPayable =
                          part.status !== "confirmed" && provider?.channel === "qr" && providerIsPayable;
                        const qrImageFailed = failedQrImages.has(part.id);
                        const paymentState =
                          part.status === "confirmed"
                            ? "Оплата подтверждена"
                            : provider?.status === "paid"
                              ? "Оплата получена, обновляем статус заказа"
                              : provider?.status === "cancelling"
                                ? "Отмена отправлена, ожидаем подтверждение"
                                : part.method === "cash"
                                  ? "Ожидает подтверждения кассиром"
                                  : providerIsPayable
                                    ? "Подтвердится автоматически после оплаты"
                                    : "Этот платёж больше не активен";

                        return (
                          <div
                            key={part.id}
                            className="rounded-xl border border-[var(--border)] bg-[var(--muted)]/25 p-4"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="flex min-w-0 items-start gap-2">
                                {part.method === "kaspi" ? (
                                  <QrCode className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
                                ) : part.method === "invoice" ? (
                                  <FileText className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
                                ) : (
                                  <Clock className="mt-0.5 size-4 shrink-0 text-[var(--warning)]" />
                                )}
                                <div className="min-w-0">
                                  <div className="font-medium">
                                    {part.method === "kaspi"
                                      ? "Kaspi QR"
                                      : part.method === "invoice"
                                        ? "Счёт на оплату"
                                        : "Наличными"}
                                    {" · "}
                                    {formatMoney(part.amount)} {currencySymbol(order.currency)}
                                  </div>
                                  <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{paymentState}</div>
                                </div>
                              </div>
                              {part.can_release && provider?.status !== "cancelling" && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={busy}
                                  onClick={() => {
                                    setReleaseError("");
                                    setReleasePart(part);
                                  }}
                                >
                                  <ArrowLeft className="size-4" />{" "}
                                  {order.payment_status === "settled" ? "Закрыть лишнюю заявку" : "Другой способ"}
                                </Button>
                              )}
                            </div>

                            {provider && (
                              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3 text-xs text-[var(--muted-foreground)]">
                                <Badge tone={providerStatusTone(provider.status)} dot>
                                  {PROVIDER_STATUS_LABELS[provider.status] ?? provider.status}
                                </Badge>
                                {provider.channel === "phone" && provider.phone_number && (
                                  <span>Счёт отправлен на {provider.phone_number}</span>
                                )}
                                {provider.channel === "qr" && provider.qr_expires_at && providerIsPayable && (
                                  <span>QR действует до {formatDateTime(provider.qr_expires_at)}</span>
                                )}
                              </div>
                            )}

                            {qrIsPayable && (
                              <>
                                {provider.qr_image_url && !qrImageFailed ? (
                                  <Image
                                    src={provider.qr_image_url}
                                    alt="Kaspi QR для оплаты"
                                    width={224}
                                    height={224}
                                    unoptimized
                                    onError={() => setFailedQrImages((current) => new Set(current).add(part.id))}
                                    className="mx-auto mt-4 size-56 rounded-2xl bg-white p-2 shadow-sm"
                                  />
                                ) : (
                                  <div className="mx-auto mt-4 flex size-56 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-[var(--border)] bg-white p-6 text-center">
                                    <QrCode className="size-12 text-[var(--primary)]" />
                                    <p className="text-sm text-[var(--muted-foreground)]">
                                      {provider.qr_token_url
                                        ? "QR готов. Откройте Kaspi кнопкой ниже."
                                        : "QR создаётся. Обновите страницу через несколько секунд."}
                                    </p>
                                  </div>
                                )}
                                {provider.qr_token_url && (
                                  <Button
                                    className="mt-4 w-full"
                                    onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener,noreferrer")}
                                  >
                                    <Smartphone className="size-4" /> Открыть Kaspi
                                  </Button>
                                )}
                              </>
                            )}
                          </div>
                        );
                      })}

                      {available > 0 && (
                        <>
                          <div className="rounded-xl border border-dashed border-[var(--border)] p-4">
                            <div className="mb-3 flex items-end justify-between gap-4">
                              <div>
                                <div className="font-medium">Добавить часть оплаты</div>
                                <div className="text-xs text-[var(--muted-foreground)]">
                                  Можно разделить остаток между несколькими способами.
                                </div>
                              </div>
                              <div className="w-40">
                                <label className="mb-1 block text-xs text-[var(--muted-foreground)]">Сумма</label>
                                <Input
                                  inputMode="decimal"
                                  value={chosenAmount}
                                  aria-invalid={Boolean(amountError)}
                                  onChange={(event) => {
                                    setPaymentAmount(event.target.value);
                                    setAmountTouched(true);
                                  }}
                                />
                              </div>
                            </div>
                            {amountTouched && amountError && (
                              <p className="mb-3 flex items-center gap-1.5 text-xs text-[var(--destructive)]">
                                <AlertTriangle className="size-3.5" /> {amountError}
                              </p>
                            )}
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                              {order.currency === "KZT" && (
                                <>
                                  <Button
                                    variant="outline"
                                    className="h-auto justify-start py-3"
                                    onClick={() => setPaymentMode("invoice")}
                                  >
                                    <FileText className="size-4" /> Счёт на оплату
                                  </Button>
                                  <Button
                                    variant="outline"
                                    className="h-auto justify-start py-3"
                                    onClick={() => setPaymentMode("qr")}
                                  >
                                    <QrCode className="size-4" /> Kaspi Pay · QR
                                  </Button>
                                </>
                              )}
                              <Button
                                disabled={busy || Boolean(amountError)}
                                variant="outline"
                                className="h-auto justify-start py-3"
                                onClick={() => run(() => selectPayment("cash", normalizedAmount), true)}
                              >
                                <Banknote className="size-4" /> Наличными
                              </Button>
                              <Button
                                disabled={busy || order.has_pending_payment}
                                variant="outline"
                                className="h-auto justify-start py-3"
                                onClick={() => run(() => selectPayment("debt"))}
                              >
                                <HandCoins className="size-4" /> В долг
                              </Button>
                            </div>
                            {order.currency !== "KZT" && (
                              <p className="mt-3 text-xs text-[var(--muted-foreground)]">
                                Для оплаты в долларах доступны наличные или оформление долга.
                              </p>
                            )}
                          </div>

                          {paymentMode && order.currency === "KZT" && (
                            <div className="rounded-xl border border-[var(--primary)]/25 bg-[var(--primary)]/5 p-4">
                              <div className="mb-3 flex items-center justify-between">
                                <div className="font-medium">
                                  {paymentMode === "qr" ? "Kaspi Pay — QR" : "Счёт на оплату"}
                                </div>
                                <Button size="sm" variant="ghost" onClick={() => setPaymentMode(null)}>
                                  <ArrowLeft className="size-4" /> Назад
                                </Button>
                              </div>
                              {paymentMode === "invoice" && (
                                <div className="mb-3">
                                  <label className="mb-1.5 block text-xs text-[var(--muted-foreground)]">
                                    Номер телефона для получения счёта
                                  </label>
                                  <Input
                                    inputMode="tel"
                                    value={phone}
                                    onChange={(event) => setPaymentPhone(event.target.value)}
                                    placeholder="8 700 000 00 00"
                                  />
                                  {!phone.trim() && (
                                    <p className="mt-1.5 text-xs text-[var(--destructive)]">
                                      Укажите номер, на который отправить счёт.
                                    </p>
                                  )}
                                </div>
                              )}
                              <Button
                                className="w-full"
                                disabled={busy || Boolean(amountError) || (paymentMode === "invoice" && !phone.trim())}
                                onClick={() =>
                                  run(async () => {
                                    await payOrder(Number(id), paymentMode === "qr" ? "kaspi" : "invoice", {
                                      amount: normalizedAmount,
                                      phone_number: paymentMode === "invoice" ? phone.trim() : undefined,
                                    });
                                  }, true)
                                }
                              >
                                {paymentMode === "qr" ? "Создать QR" : "Отправить счёт на оплату"}
                              </Button>
                            </div>
                          )}
                        </>
                      )}
                    </>
                  )}

                  <p className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                    <CheckCircle2 className="size-3.5" /> Валюта фиксирована
                    {order.currency === "KZT"
                      ? ", наличные, QR и счёт можно комбинировать."
                      : ", оплатить можно наличными или в долг."}
                  </p>
                </CardContent>
              </Card>
            );
          })()}

        {step === "truck" && (
          <Card>
            <CardHeader>
              <CardTitle>Отправка КАМАЗа</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-[var(--muted-foreground)]">
                Укажите номер КАМАЗа. Оплата станет доступна после отгрузки.
              </p>
              {order.truck_number && (
                <p className="text-sm">
                  Текущий номер: <b>{order.truck_number}</b>
                </p>
              )}
              <div className="flex gap-2">
                <Input placeholder="Номер КАМАЗа" value={truck} onChange={(e) => setTruckVal(e.target.value)} />
                <Button disabled={busy || !truck} onClick={() => run(() => setTruck(order.id, truck))}>
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
                  КАМАЗ: <b>{order.truck_number}</b>
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
