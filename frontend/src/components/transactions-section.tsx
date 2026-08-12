"use client";

import Image from "next/image";
import { useCallback, useEffect, useState } from "react";
import { Download, ExternalLink, QrCode, RefreshCcw, RotateCcw, Search, Send, Undo2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { api, apiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { cn, formatCurrency, formatDateTime, formatMoney, currencySymbol } from "@/lib/utils";
import { PAYMENT_METHOD_LABELS, PAYMENT_STAGE_LABELS, paymentStage } from "@/lib/constants";
import type { Department, Payment } from "@/lib/types";

interface TransactionPage {
  results: Payment[];
  count: number;
  page: number;
  pages: number;
  /** Счётчики по статусам при текущем поиске (до статус-фильтра). */
  status_counts?: Record<string, number>;
  summary: {
    paid_by_currency: { KZT: string; USD: string };
    refunded_by_currency: { KZT: string; USD: string };
    /** {валюта: {способ: чистая сумма}} — сумма по способам равна итогу. */
    paid_by_method: Record<string, Record<string, string>>;
  };
}

// Подписи и тона берутся из общего словаря (lib/constants): раньше здесь
// лежала своя копия, которая расходилась с остальным интерфейсом и не знала
// легаси-этап accountant_ok — он выводился сырым кодом.

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
    next: "Если доступно восстановление, верните операцию в работу; иначе создайте новую.",
  },
  awaiting_customer: {
    meaning: "QR или счёт создан и ожидает действия клиента.",
    money: "Сумма зарезервирована, но ещё не считается оплаченной.",
    next: "Ничего подтверждать вручную не нужно — статус обновится автоматически.",
  },
  cancellation_pending: {
    meaning: "Запрос отмены телефонного счёта принят и ещё обрабатывается.",
    money: "Сумма остаётся зарезервированной до окончательного статуса.",
    next: "Дождитесь автоматической сверки с платёжным сервисом.",
  },
  payment_error: {
    meaning: "Платёжный сервис не смог обработать этот QR или счёт.",
    money: "Операция не учитывается как оплата.",
    next: "Создайте новую платёжную операцию и проверьте телефон клиента.",
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

/** Разбивка итога по способам: «300 000 ₸ наличными · 400 000 ₸ QR». */
function PaidMethodSummary({ summary }: { summary?: Record<string, Record<string, string>> }) {
  const parts = Object.entries(summary ?? {}).flatMap(([currency, methods]) =>
    Object.entries(methods).map(([method, amount]) => ({ currency, method, amount })),
  );
  // Один способ ничего не добавляет к уже показанному итогу.
  if (parts.length < 2) return null;
  return (
    // Сумма и способ не должны разъезжаться по строкам — переносим парами.
    <div className="mt-2 text-xs text-[var(--muted-foreground)]">
      {parts.map((part, index) => (
        <span key={`${part.currency}-${part.method}`} className="whitespace-nowrap">
          {index > 0 && <span className="px-1.5">·</span>}
          <span className="font-medium tabular-nums text-[var(--foreground)]">
            {formatCurrency(part.amount, part.currency)}
          </span>{" "}
          {PAYMENT_METHOD_LABELS[part.method] ?? part.method}
        </span>
      ))}
    </div>
  );
}

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

const ACTIVE_PROVIDER_STATUSES = new Set(["creating", "processing", "pending", "cancelling"]);

function PaymentQrPreview({ payment, onClose }: { payment: Payment; onClose: () => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  const provider = payment.provider;
  if (!provider) return null;
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`PAY-${String(payment.id).padStart(6, "0")}`}
      title="Kaspi QR готов"
      description="Покажите QR клиенту или откройте оплату на его устройстве. Статус обновится автоматически."
      footer={<Button onClick={onClose}>Готово</Button>}
    >
      <div className="space-y-4 text-center">
        {provider.qr_image_url && !imageFailed ? (
          <Image
            src={provider.qr_image_url}
            alt="Kaspi QR для оплаты"
            width={288}
            height={288}
            unoptimized
            onError={() => setImageFailed(true)}
            className="mx-auto size-72 max-w-full rounded-2xl bg-white p-3 shadow-sm"
          />
        ) : (
          <div className="mx-auto flex aspect-square w-72 max-w-full flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--muted)]/35 p-6">
            <QrCode className="size-12 text-[var(--muted-foreground)]" />
            <p className="mt-3 text-sm text-[var(--muted-foreground)]">
              Изображение QR недоступно. Откройте оплату кнопкой ниже.
            </p>
          </div>
        )}
        {provider.qr_token_url && (
          <Button className="w-full" onClick={() => window.open(provider.qr_token_url!, "_blank", "noopener")}>
            <ExternalLink className="size-4" /> Открыть Kaspi
          </Button>
        )}
      </div>
    </Modal>
  );
}

/** Пилюли статус-фильтра: подписи из общего словаря этапов оплат. */
const STATUS_FILTERS = [
  { key: "requested", label: PAYMENT_STAGE_LABELS.requested ?? "Ожидает" },
  { key: "received", label: PAYMENT_STAGE_LABELS.received ?? "Принята" },
  { key: "confirmed", label: PAYMENT_STAGE_LABELS.confirmed ?? "Подтверждена" },
  { key: "rejected", label: PAYMENT_STAGE_LABELS.rejected ?? "Отклонена" },
];

/* ── Вкладка «Транзакции»: все платежи, возвраты и чеки ─────────────────── */
export function TransactionsSection({
  canConfirm,
  canCreate,
  departments,
}: {
  canConfirm: boolean;
  canCreate: boolean;
  departments: Department[];
}) {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [department, setDepartment] = useState("all");
  const debouncedQuery = useDebounced(query.trim());
  useEffect(() => setPage(1), [debouncedQuery, department, statusFilter]);
  const transactionParams = new URLSearchParams({
    page: String(page),
    page_size: "50",
    search: debouncedQuery,
  });
  if (statusFilter !== "all") transactionParams.set("status", statusFilter);
  if (department !== "all") transactionParams.set("department", department);
  const {
    data,
    loading,
    error: loadError,
    reload,
  } = useApi<TransactionPage>(`/payment-transactions/?${transactionParams.toString()}`);
  // Единый стиль пагинации: страницы накапливаются под «Показать ещё»,
  // а не листаются взад-вперёд. Итоги в конверте всегда по всей выборке.
  const [rows, setRows] = useState<Payment[]>([]);
  // Конверт держим отдельно от data: useApi зануляет data на время запроса,
  // а кнопка «Показать ещё» не должна пропадать, пока грузится страница.
  const [meta, setMeta] = useState<{ page: number; pages: number; count: number } | null>(null);
  useEffect(() => {
    if (!data) return;
    setMeta({ page: data.page, pages: data.pages, count: data.count });
    setRows((current) => {
      if (data.page <= 1) return data.results;
      // Смещение страниц может сдвинуться из-за новых оплат — дубликаты
      // строк (и React-ключей) отфильтровываем по id.
      const seen = new Set(current.map((row) => row.id));
      return [...current, ...data.results.filter((row) => !seen.has(row.id))];
    });
  }, [data]);
  useEffect(() => {
    // Новый поиск или статус — новый список: старые накопленные строки не
    // должны выглядеть результатом свежего запроса.
    setRows([]);
    setMeta(null);
  }, [debouncedQuery, department, statusFilter]);
  // Счётчики статусов приходят до статус-фильтра и живут между запросами,
  // чтобы пилюли не мигали на каждую загрузку.
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  useEffect(() => setStatusCounts({}), [department]);
  useEffect(() => {
    if (data?.status_counts) setStatusCounts(data.status_counts);
  }, [data]);

  // После действий (подтвердить/возврат/восстановить) лента начинается с
  // первой страницы — иначе накопленные строки разъедутся с сервером.
  const refreshFromStart = useCallback(() => {
    if (page === 1) return reload();
    setPage(1);
    return Promise.resolve();
  }, [page, reload]);
  const [refundFor, setRefundFor] = useState<Payment | null>(null);
  const [statusFor, setStatusFor] = useState<Payment | null>(null);
  const [rejectFor, setRejectFor] = useState<Payment | null>(null);
  const [restoreFor, setRestoreFor] = useState<Payment | null>(null);
  const [qrFor, setQrFor] = useState<Payment | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function receipt(payment: Payment) {
    setError("");
    try {
      const response = await api.get<Blob>(`/payment-transactions/${payment.id}/receipt/`, {
        responseType: "blob",
      });
      downloadBlob(response.data, `receipt_${payment.id}.pdf`);
    } catch (e) {
      setError(apiError(e));
    }
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
      await refreshFromStart();
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
      await refreshFromStart();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    if (!restoreFor) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${restoreFor.id}/restore/`);
      setRestoreFor(null);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await refreshFromStart();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function issue(payment: Payment) {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${payment.id}/issue/`);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await refreshFromStart();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
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
            {/* Из чего сложился итог: касса сразу видит нал/QR/счёт. */}
            <PaidMethodSummary summary={data?.summary.paid_by_method} />
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

      {error && !refundFor && !rejectFor && !restoreFor && (
        <div className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2 text-sm text-[var(--destructive)]">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>Все платежи, возвраты и чеки</CardTitle>
          <Button variant="outline" size="sm" onClick={() => void refreshFromStart()}>
            <RefreshCcw className="size-4" /> Обновить
          </Button>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative max-w-sm flex-1 basis-64">
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
            <FilterDropdown
              label="Отдел"
              active={department}
              onChange={(value) => {
                setPage(1);
                setDepartment(value);
              }}
              options={[
                { key: "all", label: "Все" },
                ...departments.map((row) => ({ key: row.code, label: row.name })),
              ]}
            />
            {/* Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */}
            <div className="flex flex-wrap gap-1.5">
              {[
                { key: "all", label: "Все", count: Object.values(statusCounts).reduce((s, n) => s + n, 0) },
                ...STATUS_FILTERS.map((item) => ({ ...item, count: statusCounts[item.key] ?? 0 })),
              ].map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setStatusFilter(item.key)}
                  aria-pressed={statusFilter === item.key}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                    statusFilter === item.key
                      ? "border-[var(--foreground)] bg-[var(--muted)]"
                      : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--foreground)]/40",
                  )}
                >
                  {item.label}
                  <span className="tabular-nums">{item.count}</span>
                </button>
              ))}
            </div>
          </div>
          {/* Спиннер на весь блок — только пока нет ни одной строки: догрузка
              следующих страниц не должна прятать уже показанное. */}
          {((loading && rows.length === 0) || loadError) && (
            <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={reload} />
          )}
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
                    const state = paymentStage(effectiveStatus);
                    return (
                      <TR key={row.id}>
                        <TD>
                          <div className="font-medium">PAY-{String(row.id).padStart(6, "0")}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.paid_at)}</div>
                        </TD>
                        <TD>{row.client_name ?? "—"}</TD>
                        <TD>
                          {row.method_label ?? row.method}
                          {row.provider?.channel === "qr" && (
                            <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                          )}
                        </TD>
                        <TD className="font-medium tabular-nums">
                          {formatMoney(row.amount)} {currencySymbol(row.currency)}
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
                              {formatMoney(row.refunded_amount ?? 0)} {currencySymbol(row.currency)}
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
                            {canConfirm && row.can_restore && (
                              <Button
                                size="sm"
                                variant="outline"
                                title="Восстановить отклонённую операцию"
                                onClick={() => {
                                  setError("");
                                  setRestoreFor(row);
                                }}
                              >
                                <Undo2 className="size-4" />
                                <span className="hidden xl:inline">Восстановить</span>
                              </Button>
                            )}
                            {canCreate && row.can_issue && (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                title="Отправить счёт клиенту"
                                onClick={() => void issue(row)}
                              >
                                <Send className="size-4" />
                                <span className="hidden xl:inline">Отправить</span>
                              </Button>
                            )}
                            {row.provider?.channel === "qr" &&
                              row.provider.qr_token_url &&
                              ACTIVE_PROVIDER_STATUSES.has(row.provider.status) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title="Открыть активный Kaspi QR"
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
                            {canConfirm && row.status === "confirmed" && Number(row.available_for_refund ?? 0) > 0 && (
                              <Button
                                size="sm"
                                variant="ghost"
                                title={row.provider ? "Вернуть через ApiPay" : "Вернуть деньги из кассы"}
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
                            {canConfirm &&
                              ["requested", "received"].includes(row.status) &&
                              row.confirmation_mode !== "automatic" && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title="Отклонить платёж"
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
              <LoadMore
                shown={rows.length}
                total={meta?.count ?? rows.length}
                hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
                loading={loading && page > 1}
                onClick={() => setPage((value) => value + 1)}
              />
            </>
          )}
        </CardContent>
      </Card>

      <Modal
        open={!!refundFor}
        onClose={() => !busy && setRefundFor(null)}
        eyebrow={refundFor?.provider ? "ApiPay · Возврат" : "Касса · Возврат"}
        title="Вернуть оплату"
        description={
          refundFor?.provider
            ? "Возврат будет отправлен через ApiPay. Деньги учтутся после подтверждения платёжного сервиса."
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
        title={statusFor ? paymentStage(statusFor.effective_status ?? statusFor.status).label : "Статус"}
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
                {formatMoney(statusFor.amount)} {currencySymbol(statusFor.currency)}
              </div>
              <div className="mt-2 space-y-0.5 text-xs text-[var(--muted-foreground)]">
                {statusFor.provider && (
                  <div>
                    Состояние счёта: {statusFor.provider.status}
                    {statusFor.provider.phone_number ? ` · ${statusFor.provider.phone_number}` : ""}
                  </div>
                )}
                {statusFor.note && <div>Примечание: {statusFor.note}</div>}
              </div>
            </div>
            <StatusExplanation status={statusFor.effective_status ?? statusFor.status} />
            {/* Журнал операции — для любого способа, включая наличные: раньше
                историю имели только онлайн-счета, и наличная транзакция
                выглядела безымянной. */}
            <div>
              <div className="mb-2 text-sm font-medium">Журнал операции</div>
              <div className="space-y-1.5">
                {[
                  {
                    key: "created",
                    label: "Создан",
                    at: statusFor.paid_at,
                    by: statusFor.recorded_by_name || "Система",
                  },
                  ...(statusFor.received_at
                    ? [
                        {
                          key: "received",
                          label: "Принят кассой",
                          at: statusFor.received_at,
                          by: statusFor.received_by_name,
                        },
                      ]
                    : []),
                  ...(statusFor.confirmed_at
                    ? [
                        {
                          key: "confirmed",
                          label:
                            statusFor.confirmation_mode === "automatic" ? "Подтверждён автоматически" : "Подтверждён",
                          at: statusFor.confirmed_at,
                          by:
                            statusFor.confirmation_mode === "automatic"
                              ? "Платёжный сервис"
                              : statusFor.confirmed_by_name,
                        },
                      ]
                    : []),
                  ...(statusFor.status === "rejected"
                    ? [{ key: "rejected", label: "Отклонён", at: null, by: null }]
                    : []),
                ].map((step) => (
                  <div key={step.key} className="flex items-baseline gap-2 text-sm">
                    <span
                      className={cn(
                        "size-1.5 shrink-0 translate-y-[-1px] rounded-full",
                        step.key === "rejected" ? "bg-[var(--destructive)]" : "bg-[var(--success)]",
                      )}
                    />
                    <span className="font-medium">{step.label}</span>
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {step.at ? formatDateTime(step.at) : ""}
                      {step.by ? ` · ${step.by}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            {(statusFor.refunds?.length ?? 0) > 0 && (
              <div>
                <div className="mb-2 text-sm font-medium">История возвратов</div>
                <div className="space-y-2">
                  {statusFor.refunds!.map((refund) => (
                    <div key={refund.id} className="rounded-lg border px-3 py-2 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium">
                          {formatMoney(refund.amount)} {currencySymbol(statusFor.currency)}
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
              Заказ #{rejectFor?.order} · {formatMoney(rejectFor?.amount ?? 0)} {currencySymbol(rejectFor?.currency)}
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

      <ConfirmDialog
        open={!!restoreFor}
        onClose={() => {
          if (!busy) {
            setRestoreFor(null);
            setError("");
          }
        }}
        title={`Восстановить PAY-${String(restoreFor?.id ?? "").padStart(6, "0")}?`}
        description={
          restoreFor?.method === "invoice"
            ? "Операция снова зарезервирует сумму заказа, после чего новый счёт будет отправлен клиенту."
            : "Операция вернётся в очередь кассира. Восстановление доступно только в пределах свободного остатка заказа."
        }
        confirmLabel="Восстановить"
        confirmVariant="default"
        busy={busy}
        error={error}
        onConfirm={() => void restore()}
      />

      {qrFor && <PaymentQrPreview payment={qrFor} onClose={() => setQrFor(null)} />}
    </section>
  );
}
