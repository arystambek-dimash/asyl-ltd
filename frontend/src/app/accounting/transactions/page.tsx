"use client";

import { useState } from "react";
import { Download, ExternalLink, RefreshCcw, RotateCcw, Search, XCircle } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import type { Payment } from "@/lib/types";

interface TransactionPage {
  results: Payment[];
  count: number;
  page: number;
  pages: number;
  summary: {
    paid_by_currency: { KZT: string; USD: string };
    refunded_by_currency: { KZT: string; USD: string };
  };
}

const STATUS: Record<string, { label: string; tone: "success" | "warning" | "destructive" | "muted" | "primary" }> = {
  confirmed: { label: "Оплачено", tone: "success" },
  received: { label: "В кассе", tone: "warning" },
  requested: { label: "Ожидает", tone: "warning" },
  rejected: { label: "Отклонено", tone: "destructive" },
  refund_pending: { label: "Возврат в обработке", tone: "warning" },
  partially_refunded: { label: "Частично возвращено", tone: "primary" },
  refunded: { label: "Возвращено", tone: "muted" },
};

const STATUS_HELP: Record<string, { meaning: string; money: string; next: string }> = {
  requested: {
    meaning: "Оплата создана, но сотрудник ещё не подтвердил получение.",
    money: "Не учитывается в оплаченной сумме заказа.",
    next: "Можно принять оплату или отклонить её.",
  },
  received: {
    meaning: "Сотрудник принял операцию, бухгалтер ещё не подтвердил деньги.",
    money: "Пока не учитывается в оплаченной сумме заказа.",
    next: "Бухгалтер может подтвердить или отклонить операцию.",
  },
  confirmed: {
    meaning: "Оплата подтверждена и деньги поступили.",
    money: "Полностью учитывается в кассе и уменьшает долг заказа.",
    next: "Можно скачать выписку или оформить полный/частичный возврат.",
  },
  rejected: {
    meaning: "Операция отклонена и закрыта без оплаты.",
    money: "Не учитывается в кассе и не уменьшает долг.",
    next: "Если клиент платит заново, создайте новую операцию.",
  },
  refund_pending: {
    meaning: "Запрос возврата по счёту отправлен и ожидает результата.",
    money: "До подтверждения провайдера оплата ещё учитывается.",
    next: "Дождитесь подтверждения платёжного сервиса — статус обновится автоматически.",
  },
  partially_refunded: {
    meaning: "Клиенту возвращена часть оплаты.",
    money: "В кассе учитывается только остаток после возврата.",
    next: "Можно вернуть оставшуюся доступную сумму.",
  },
  refunded: {
    meaning: "Оплата возвращена клиенту полностью.",
    money: "Больше не учитывается в кассе и снова увеличивает остаток заказа.",
    next: "Повторный возврат для этой операции недоступен.",
  },
};

function StatusExplanation({ status }: { status: string }) {
  const help = STATUS_HELP[status] ?? {
    meaning: "Технический статус платёжной операции.",
    money: "Проверьте детали операции и историю возвратов.",
    next: "Обновите страницу для получения актуального состояния.",
  };
  return (
    <div className="space-y-2 text-sm">
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Что означает: </span>
        <span className="text-[var(--muted-foreground)]">{help.meaning}</span>
      </div>
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Деньги: </span>
        <span className="text-[var(--muted-foreground)]">{help.money}</span>
      </div>
      <div className="rounded-lg border px-3 py-2.5">
        <span className="font-medium">Что дальше: </span>
        <span className="text-[var(--muted-foreground)]">{help.next}</span>
      </div>
    </div>
  );
}

export default function TransactionsPage() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const {
    data,
    loading,
    error: loadError,
    reload,
  } = useApi<TransactionPage>(
    `/payment-transactions/?page=${page}&page_size=50&search=${encodeURIComponent(query.trim())}`,
  );
  const [refundFor, setRefundFor] = useState<Payment | null>(null);
  const [statusFor, setStatusFor] = useState<Payment | null>(null);
  const [rejectFor, setRejectFor] = useState<Payment | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rows = data?.results ?? [];

  async function receipt(payment: Payment) {
    const response = await api.get<Blob>(`/payment-transactions/${payment.id}/receipt/`, {
      responseType: "blob",
    });
    downloadBlob(response.data, `receipt_${payment.id}.pdf`);
  }

  async function refund() {
    if (!refundFor) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${refundFor.id}/refund/`, {
        amount: amount || undefined,
        reason,
        mode: "auto",
      });
      setRefundFor(null);
      setAmount("");
      setReason("");
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!rejectFor) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${rejectFor.id}/reject/`, {
        reason: rejectReason,
      });
      setRejectFor(null);
      setRejectReason("");
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Транзакции">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Операций</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{data?.count ?? 0}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Оплачено</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--success)]">
                {formatMoney(data?.summary.paid_by_currency.KZT ?? 0)} ₸
                {Number(data?.summary.paid_by_currency.USD ?? 0) > 0 && (
                  <span className="ml-2 text-base text-[var(--muted-foreground)]">
                    + {formatMoney(data!.summary.paid_by_currency.USD)} $
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-5">
              <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Возвращено</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">
                {formatMoney(data?.summary.refunded_by_currency.KZT ?? 0)} ₸
                {Number(data?.summary.refunded_by_currency.USD ?? 0) > 0 && (
                  <span className="ml-2 text-base text-[var(--muted-foreground)]">
                    + {formatMoney(data!.summary.refunded_by_currency.USD)} $
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3">
            <CardTitle>Все платежи, возвраты и чеки</CardTitle>
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              <RefreshCcw className="size-4" /> Обновить
            </Button>
          </CardHeader>
          <CardContent>
            <div className="relative mb-4 max-w-sm">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                className="pl-9"
                placeholder="Клиент, заказ или операция"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            {(loading || loadError) && <DataGate loading={loading} error={loadError} onRetry={reload} />}
            {!loading && !loadError && rows.length === 0 && (
              <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
            )}
            {rows.length > 0 && (
              <>
                <Table>
                  <THead>
                    <TR>
                      <TH>Операция</TH>
                      <TH>Клиент</TH>
                      <TH>Способ</TH>
                      <TH>Сумма</TH>
                      <TH>Статус</TH>
                      <TH>Возврат</TH>
                      <TH />
                    </TR>
                  </THead>
                  <TBody>
                    {rows.map((row) => {
                      const effectiveStatus = row.effective_status ?? row.status;
                      const state = STATUS[effectiveStatus] ?? {
                        label: effectiveStatus,
                        tone: "muted" as const,
                      };
                      return (
                        <TR key={row.id}>
                          <TD>
                            <div className="font-medium">PAY-{String(row.id).padStart(6, "0")}</div>
                            <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                          </TD>
                          <TD>{row.client_name ?? "—"}</TD>
                          <TD>
                            {row.method_label ?? row.method}
                            {row.provider?.channel === "qr" && (
                              <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                            )}
                          </TD>
                          <TD className="font-medium tabular-nums">
                            {formatMoney(row.amount)} {row.currency === "USD" ? "$" : "₸"}
                          </TD>
                          <TD>
                            <button
                              type="button"
                              onClick={() => setStatusFor(row)}
                              className="rounded-md outline-none ring-offset-2 hover:opacity-80 focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                              title="Нажмите, чтобы узнать значение статуса"
                            >
                              <Badge tone={state.tone}>{state.label}</Badge>
                            </button>
                          </TD>
                          <TD>
                            {Number(row.refunded_amount ?? 0) > 0 ? (
                              <span className="text-sm tabular-nums">
                                {formatMoney(row.refunded_amount ?? 0)} {row.currency === "USD" ? "$" : "₸"}
                              </span>
                            ) : Number(row.pending_refund_amount ?? 0) > 0 ? (
                              <span className="text-sm text-[var(--warning)]">
                                {formatMoney(row.pending_refund_amount ?? 0)} в обработке
                              </span>
                            ) : (
                              "—"
                            )}
                          </TD>
                          <TD>
                            <div className="flex justify-end gap-1">
                              {row.provider?.qr_token_url && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => window.open(row.provider!.qr_token_url!, "_blank", "noopener")}
                                >
                                  <ExternalLink className="size-4" />
                                </Button>
                              )}
                              {row.status === "confirmed" && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title="Скачать выписку ASYL LTD"
                                  onClick={() => void receipt(row)}
                                >
                                  <Download className="size-4" />
                                </Button>
                              )}
                              {row.status === "confirmed" && Number(row.available_for_refund ?? 0) > 0 && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title={
                                    row.provider?.channel === "phone" ? "Вернуть по счёту" : "Вернуть деньги из кассы"
                                  }
                                  onClick={() => {
                                    setError("");
                                    setRefundFor(row);
                                    setAmount(row.available_for_refund ?? "");
                                    setReason("");
                                  }}
                                >
                                  <RotateCcw className="size-4" />
                                </Button>
                              )}
                              {["requested", "received"].includes(row.status) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={row.provider?.channel === "qr"}
                                  title={
                                    row.provider?.channel === "qr"
                                      ? "Активный Kaspi QR нельзя отменить — дождитесь истечения"
                                      : "Отклонить платёж"
                                  }
                                  className="text-[var(--destructive)]"
                                  onClick={() => {
                                    setError("");
                                    setRejectFor(row);
                                    setRejectReason("");
                                  }}
                                >
                                  <XCircle className="size-4" />
                                </Button>
                              )}
                            </div>
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
                <div className="mt-4 flex items-center justify-between border-t pt-4">
                  <span className="text-xs text-[var(--muted-foreground)]">
                    Страница {data?.page ?? 1} из {data?.pages ?? 1}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={(data?.page ?? 1) <= 1}
                      onClick={() => setPage((value) => value - 1)}
                    >
                      Назад
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={(data?.page ?? 1) >= (data?.pages ?? 1)}
                      onClick={() => setPage((value) => value + 1)}
                    >
                      Далее
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Modal
        open={!!refundFor}
        onClose={() => !busy && setRefundFor(null)}
        eyebrow={refundFor?.provider?.channel === "phone" ? "Счёт на оплату · Возврат" : "Касса · Возврат"}
        title="Вернуть оплату"
        description={
          refundFor?.provider?.channel === "phone"
            ? "Возврат будет отправлен по счёту. Деньги учтутся после подтверждения платёжного сервиса."
            : "Возврат будет сразу проведён как выдача денег из кассы и уменьшит оплаченную сумму заказа."
        }
        footer={
          <>
            <Button variant="outline" onClick={() => setRefundFor(null)}>
              Отмена
            </Button>
            <Button disabled={busy || !amount || !reason.trim()} onClick={() => void refund()}>
              {busy ? "Отправка…" : "Оформить возврат"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div>
            <label className="mb-1.5 block text-sm">Сумма возврата</label>
            <Input type="number" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина</label>
            <Input
              maxLength={500}
              placeholder="Например: возврат товара"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>
      </Modal>

      <Modal
        open={!!statusFor}
        onClose={() => setStatusFor(null)}
        eyebrow="Статус операции"
        title={STATUS[statusFor?.effective_status ?? statusFor?.status ?? ""]?.label ?? statusFor?.status ?? "Статус"}
        description="Статус показывает, учитываются ли деньги в кассе и что можно сделать с операцией."
        footer={<Button onClick={() => setStatusFor(null)}>Понятно</Button>}
      >
        {statusFor && (
          <div className="space-y-4">
            <div className="rounded-xl border bg-[var(--muted)]/35 p-4">
              <div className="text-sm font-medium">
                {statusFor.client_name} · заказ #{statusFor.order}
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">
                {formatMoney(statusFor.amount)} {statusFor.currency === "USD" ? "$" : "₸"}
              </div>
            </div>
            <StatusExplanation status={statusFor.effective_status ?? statusFor.status} />
            {(statusFor.refunds?.length ?? 0) > 0 && (
              <div>
                <div className="mb-2 text-sm font-medium">История возвратов</div>
                <div className="space-y-2">
                  {statusFor.refunds!.map((refund) => (
                    <div key={refund.id} className="rounded-lg border px-3 py-2 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium">
                          {formatMoney(refund.amount)} {statusFor.currency === "USD" ? "$" : "₸"}
                        </span>
                        <span className="text-xs text-[var(--muted-foreground)]">
                          {refund.status === "completed"
                            ? "Завершён"
                            : refund.status === "pending"
                              ? "В обработке"
                              : "Ошибка"}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-[var(--muted-foreground)]">
                        {refund.method === "apipay" ? "По счёту" : "Из кассы"} · {refund.reason}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        open={!!rejectFor}
        onClose={() => !busy && setRejectFor(null)}
        eyebrow="Касса · Контроль операции"
        title={`Отклонить PAY-${String(rejectFor?.id ?? "").padStart(6, "0")}?`}
        description="Платёж не будет учтён. Для телефонного счёта сначала будет запрошена отмена счёта на оплату."
        footer={
          <>
            <Button variant="outline" disabled={busy} onClick={() => setRejectFor(null)}>
              Не отклонять
            </Button>
            <Button variant="destructive" disabled={busy || !rejectReason.trim()} onClick={() => void reject()}>
              {busy ? "Отклонение…" : "Отклонить платёж"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3 text-sm">
            <div className="font-medium">{rejectFor?.client_name}</div>
            <div className="mt-1 text-[var(--muted-foreground)]">
              Заказ #{rejectFor?.order} · {formatMoney(rejectFor?.amount ?? 0)}{" "}
              {rejectFor?.currency === "USD" ? "$" : "₸"}
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-sm">Причина отклонения</label>
            <Input
              autoFocus
              maxLength={500}
              placeholder="Например: ошибочно внесённая оплата"
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
            />
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
