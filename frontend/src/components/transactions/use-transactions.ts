"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiError, blobApiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import type { Payment, QrRefundState } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { useQrRefundWindow } from "./qr-refund-modal";

interface TransactionPage {
  results: Payment[];
  count: number;
  page: number;
  pages: number;
  /** Счётчики по статусам при текущем поиске (до статус-фильтра). */
  status_counts?: Record<string, number>;
  /** Подписи пилюль статус-фильтра в их порядке — из labels.py. */
  status_labels: Record<string, string>;
  summary: {
    paid_by_currency: { KZT: string; USD: string };
    refunded_by_currency: { KZT: string; USD: string };
    /** {валюта: {способ: чистая сумма}} — сумма по способам равна итогу. */
    paid_by_method: Record<string, Record<string, string>>;
    method_labels: Record<string, string>;
  };
}

/** Окна ленты взаимоисключающие: новое заменяет открытое, в том числе шторку статуса. */
export type TransactionDialogKind = "status" | "refund" | "reject" | "restore" | "reopen" | "qr";
type TransactionDialog = { kind: TransactionDialogKind; payment: Payment };

// Ошибку своего действия эти окна показывают у себя, а не на странице.
const DIALOGS_WITH_ERROR = new Set<TransactionDialogKind>(["reject", "restore", "reopen"]);

/** После восстановления или отправки счёта по Kaspi QR сразу показываем QR. */
const qrDialog = (payment: Payment): TransactionDialog | null =>
  payment.provider?.channel === "qr" ? { kind: "qr", payment } : null;

/** Данные и действия ленты транзакций — общие для десктопной таблицы и мобильного списка. */
export function useTransactions({
  department: scopeDepartment,
}: {
  /** Касса на телефоне задаёт отдел переключателем в шапке — свой фильтр ленты тогда не нужен. */
  department?: string;
} = {}) {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [ownDepartment, setDepartment] = useState("all");
  const department = scopeDepartment ?? ownDepartment;
  const debouncedQuery = useDebounced(query.trim());
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
  // а итоги и кнопка «Показать ещё» не должны пропадать, пока грузится страница.
  const [meta, setMeta] = useState<Pick<TransactionPage, "page" | "pages" | "count" | "summary"> | null>(null);
  useEffect(() => {
    if (!data) return;
    setMeta({ page: data.page, pages: data.pages, count: data.count, summary: data.summary });
    setRows((current) => {
      if (data.page <= 1) return data.results;
      // Смещение страниц может сдвинуться из-за новых оплат — дубликаты
      // строк (и React-ключей) отфильтровываем по id.
      const seen = new Set(current.map((row) => row.id));
      return [...current, ...data.results.filter((row) => !seen.has(row.id))];
    });
  }, [data]);
  useEffect(() => {
    // Новый поиск или статус — новый список с первой страницы: старые
    // накопленные строки не должны выглядеть результатом свежего запроса.
    setPage(1);
    setRows([]);
    setMeta(null);
  }, [debouncedQuery, department, statusFilter]);
  // Счётчики статусов приходят до статус-фильтра и живут между запросами,
  // чтобы пилюли не мигали на каждую загрузку.
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [statusLabels, setStatusLabels] = useState<Record<string, string>>({});
  useEffect(() => setStatusCounts({}), [department]);
  useEffect(() => {
    if (data?.status_counts) setStatusCounts(data.status_counts);
    if (data?.status_labels) setStatusLabels(data.status_labels);
  }, [data]);
  const statusItems = [
    { key: "all", label: "Все", count: Object.values(statusCounts).reduce((s, n) => s + n, 0) },
    ...Object.entries(statusLabels).map(([key, label]) => ({ key, label, count: statusCounts[key] ?? 0 })),
  ];

  // После действий (подтвердить/возврат/восстановить) лента начинается с
  // первой страницы — иначе накопленные строки разъедутся с сервером.
  const refreshFromStart = useCallback(() => {
    if (page === 1) return reload();
    setPage(1);
    return Promise.resolve();
  }, [page, reload]);
  function loadNextPage() {
    setPage((value) => value + 1);
  }
  const [dialog, setDialog] = useState<TransactionDialog | null>(null);
  // Возврат по Kaspi QR: ответ POST сразу показывает ссылку, дальше окно опрашивает сервер.
  const qrRefund = useQrRefundWindow(refreshFromStart);
  const [rejectReason, setRejectReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mutationInFlight = useRef(false);
  useVisiblePolling(reload, 15_000, page === 1 && !busy);

  async function receipt(payment: Payment) {
    setError("");
    try {
      const response = await api.get<Blob>(`/payment-transactions/${payment.id}/receipt/`, {
        responseType: "blob",
      });
      downloadBlob(response.data, `receipt_${payment.id}.pdf`);
    } catch (e) {
      setError(await blobApiError(e));
    }
  }

  /** Возврат оформлен в окне PaymentRefundModal: QR-ссылка покупателю — в своё окно, лента — с начала. */
  async function refunded(payment: Payment, initial: QrRefundState | null) {
    setDialog(null);
    if (initial) qrRefund.start(payment, initial);
    await refreshFromStart();
  }

  /**
   * Мутация по операции — одна за раз. Удачная закрывает окно (или открывает
   * следующее, которое вернула `fn`) и начинает ленту с первой страницы.
   */
  async function act(fn: () => Promise<TransactionDialog | null>) {
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      setDialog(await fn());
      await refreshFromStart();
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  const reject = (payment: Payment) =>
    act(async () => {
      await api.post(`/orders/${payment.order}/payments/${payment.id}/reject/`, { reason: rejectReason });
      return null;
    });
  const restore = (payment: Payment) =>
    act(async () => qrDialog((await api.post<Payment>(`/payment-transactions/${payment.id}/restore/`)).data));
  const reopen = (payment: Payment) =>
    act(async () => {
      await api.post(`/orders/${payment.order}/payments/${payment.id}/reopen/`);
      return null;
    });
  // Счёт уходит без своего окна: шторку статуса закрываем, ошибка — на страницу.
  const issue = (payment: Payment) =>
    act(async () => {
      setDialog(null);
      return qrDialog((await api.post<Payment>(`/payment-transactions/${payment.id}/issue/`)).data);
    });

  const pageError = dialog && DIALOGS_WITH_ERROR.has(dialog.kind) ? "" : error;

  // Ошибка прошлого действия не должна висеть в новом окне; шторка статуса
  // своих ошибок не показывает, поэтому страничную ошибку не трогает.
  function open(kind: TransactionDialogKind, payment: Payment) {
    if (kind !== "status") setError("");
    if (kind === "reject") setRejectReason("");
    setDialog({ kind, payment });
  }
  function openQrRefund(payment: Payment) {
    setError("");
    setDialog(null);
    qrRefund.start(payment, null);
  }
  const close = () => setDialog(null);

  return {
    page,
    query,
    setQuery,
    statusFilter,
    setStatusFilter,
    department,
    setDepartment,
    loading,
    loadError,
    reload,
    rows,
    meta,
    statusItems,
    refreshFromStart,
    loadNextPage,
    busy,
    error,
    pageError,
    setError,
    receipt,
    issue,
    refunded,
    reject,
    restore,
    reopen,
    dialog,
    open,
    close,
    openQrRefund,
    qrRefund,
    rejectReason,
    setRejectReason,
  };
}

export type Transactions = ReturnType<typeof useTransactions>;
