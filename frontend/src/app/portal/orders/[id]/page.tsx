"use client";
import { use, useState } from "react";
import Image from "next/image";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ProgressBar } from "@/components/ui/progress-bar";
import { DataGate } from "@/components/ui/data-state";
import { Banknote, CheckCircle2, Clock, FileText, HandCoins, QrCode, Smartphone } from "lucide-react";
import { useApi } from "@/lib/use-api";
import { apiError } from "@/lib/api";
import { currencySymbol, formatMoney, formatPortalMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { clientStep, downloadInvoice, payOrder, setTruck } from "@/lib/portal-actions";
import type { PortalOrder, PortalPaymentMethod } from "@/lib/types";

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, error: loadError, reload } = useApi<PortalOrder>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [kaspiMode, setKaspiMode] = useState<"qr" | "phone" | null>(null);
  const [kaspiPhone, setKaspiPhone] = useState("");

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

  async function selectPayment(method: PortalPaymentMethod) {
    const result = await payOrder(Number(id), method);
    if (method === "invoice") await downloadInvoice(Number(id));
    if (result.payment_redirect_url) window.location.assign(result.payment_redirect_url);
  }

  if (!order)
    return (
      <AppShell title="Заказ" portal>
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const step = clientStep(order.status, order.payment_status);
  const remaining = order.remaining_amount == null ? 0 : Number(order.remaining_amount);
  const phone = kaspiPhone || order.client_phone || "";

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
                  ) : order.apipay_invoice &&
                    ["creating", "processing", "pending"].includes(order.apipay_invoice.status) ? (
                    <div className="rounded-xl border border-[var(--primary)]/25 bg-[var(--primary)]/5 p-4 text-sm">
                      <div className="flex items-start gap-2">
                        <Smartphone className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
                        <div>
                          <div className="font-medium">
                            {order.apipay_invoice.channel === "qr"
                              ? "Kaspi QR готов к оплате"
                              : "Счёт отправлен в Kaspi"}
                          </div>
                          <div className="mt-0.5 text-[var(--muted-foreground)]">
                            Статус оплаты обновится автоматически.
                          </div>
                        </div>
                      </div>
                      {order.apipay_invoice.qr_image_url && (
                        <Image
                          src={order.apipay_invoice.qr_image_url}
                          alt="Kaspi QR для оплаты"
                          width={224}
                          height={224}
                          unoptimized
                          className="mx-auto mt-4 size-56 rounded-2xl bg-white p-2 shadow-sm"
                        />
                      )}
                      {order.apipay_invoice.qr_token_url && (
                        <Button
                          className="mt-4 w-full"
                          onClick={() => window.location.assign(order.apipay_invoice!.qr_token_url!)}
                        >
                          <Smartphone className="size-4" /> Открыть Kaspi
                        </Button>
                      )}
                    </div>
                  ) : order.has_pending_payment ? (
                    <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 p-3 text-sm text-[var(--warning)]">
                      <Clock className="mt-0.5 size-4 shrink-0" />
                      Заявка на оплату отправлена. Ожидает подтверждения сотрудником.
                    </div>
                  ) : (
                    <>
                      <p className="text-sm text-[var(--muted-foreground)]">
                        Заказ отгружен. Выберите способ оплаты в {order.currency === "USD" ? "USD ($)" : "KZT (₸)"}.
                      </p>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <Button
                          disabled={busy}
                          variant="outline"
                          className="h-auto justify-start py-3"
                          onClick={() => run(() => selectPayment("invoice"))}
                        >
                          <FileText className="size-4" /> Счет на оплату
                        </Button>
                        <Button
                          disabled={busy}
                          variant="outline"
                          className="h-auto justify-start py-3"
                          onClick={() => {
                            setKaspiPhone(order.client_phone || "");
                            setKaspiMode("qr");
                          }}
                        >
                          <Smartphone className="size-4" /> Kaspi Pay
                        </Button>
                        <Button
                          disabled={busy}
                          variant="outline"
                          className="h-auto justify-start py-3"
                          onClick={() => run(() => selectPayment("cash"))}
                        >
                          <Banknote className="size-4" /> Наличными
                        </Button>
                        <Button
                          disabled={busy}
                          variant="outline"
                          className="h-auto justify-start py-3"
                          onClick={() => run(() => selectPayment("debt"))}
                        >
                          <HandCoins className="size-4" /> В долг
                        </Button>
                      </div>
                      {kaspiMode && (
                        <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)]/35 p-4">
                          <div className="mb-3 flex gap-2">
                            <Button
                              size="sm"
                              variant={kaspiMode === "qr" ? "default" : "outline"}
                              onClick={() => setKaspiMode("qr")}
                            >
                              <QrCode className="size-4" /> QR / открыть Kaspi
                            </Button>
                            <Button
                              size="sm"
                              variant={kaspiMode === "phone" ? "default" : "outline"}
                              onClick={() => setKaspiMode("phone")}
                            >
                              <Smartphone className="size-4" /> На номер
                            </Button>
                          </div>
                          {kaspiMode === "phone" && (
                            <div className="mb-3">
                              <label className="mb-1.5 block text-xs text-[var(--muted-foreground)]">
                                Номер Kaspi — можно изменить, если платит другой человек
                              </label>
                              <Input
                                inputMode="tel"
                                value={phone}
                                onChange={(event) => setKaspiPhone(event.target.value)}
                                placeholder="8 700 000 00 00"
                              />
                            </div>
                          )}
                          <Button
                            className="w-full"
                            disabled={busy || (kaspiMode === "phone" && !phone)}
                            onClick={() =>
                              run(async () => {
                                const result = await payOrder(Number(id), "kaspi", {
                                  channel: kaspiMode,
                                  phone_number: kaspiMode === "phone" ? phone : undefined,
                                });
                                if (result.payment_redirect_url) {
                                  window.location.assign(result.payment_redirect_url);
                                }
                              })
                            }
                          >
                            {kaspiMode === "qr" ? "Перейти к оплате" : "Отправить счёт в Kaspi"}
                          </Button>
                        </div>
                      )}
                    </>
                  )}

                  <p className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                    <CheckCircle2 className="size-3.5" /> Способ и валюта оплаты фиксируются в заказе.
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
            <CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              {order.truck_number && (
                <p className="mb-1">
                  КАМАЗ: <b>{order.truck_number}</b>
                </p>
              )}
              {step === "done" ? "Заказ отгружен." : "Заказ в обработке на складе."}
            </CardContent>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
