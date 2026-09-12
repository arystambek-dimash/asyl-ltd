"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import { PAYMENT_STAGE_LABELS } from "@/lib/constants";
import { downloadBlob } from "@/lib/download";
import type { Payment } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { useVisiblePolling } from "@/lib/use-visible-polling";

export interface TransactionPage {
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

/** Пилюли статус-фильтра: подписи из общего словаря этапов оплат. */
export const STATUS_FILTERS = [
  { key: "requested", label: PAYMENT_STAGE_LABELS.requested ?? "Ожидает" },
  { key: "received", label: PAYMENT_STAGE_LABELS.received ?? "Принята" },
  { key: "confirmed", label: PAYMENT_STAGE_LABELS.confirmed ?? "Подтверждена" },
  { key: "rejected", label: PAYMENT_STAGE_LABELS.rejected ?? "Отклонена" },
];

/** Данные и действия ленты транзакций — общие для десктопной таблицы и мобильного списка. */
export function useTransactions({
  onChanged,
  department: scopeDepartment,
}: {
  onChanged?: () => Promise<unknown>;
  /** Касса на телефоне задаёт отдел переключателем в шапке — свой фильтр ленты тогда не нужен. */
  department?: string;
} = {}) {
  const [page, setPage] = useState(1);
  const [query, setQueryState] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [ownDepartment, setDepartmentState] = useState("all");
  const department = scopeDepartment ?? ownDepartment;
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
  const statusItems = [
    { key: "all", label: "Все", count: Object.values(statusCounts).reduce((s, n) => s + n, 0) },
    ...STATUS_FILTERS.map((item) => ({ ...item, count: statusCounts[item.key] ?? 0 })),
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
  const mutationInFlight = useRef(false);
  useVisiblePolling(reload, 15_000, page === 1 && !busy);

  function setQuery(value: string) {
    setQueryState(value);
    setPage(1);
  }
  function setDepartment(value: string) {
    setPage(1);
    setDepartmentState(value);
  }

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
    if (!refundFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
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
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function reject() {
    if (!rejectFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/payment-transactions/${rejectFor.id}/reject/`, {
        reason: rejectReason,
      });
      setRejectFor(null);
      setRejectReason("");
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function restore() {
    if (!restoreFor || mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${restoreFor.id}/restore/`);
      setRestoreFor(null);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  async function issue(payment: Payment) {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Payment>(`/payment-transactions/${payment.id}/issue/`);
      if (response.data.provider?.channel === "qr") setQrFor(response.data);
      await Promise.all([refreshFromStart(), onChanged?.()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  // Открыватели модалок: ошибка предыдущего действия не должна висеть в новой.
  function openRefund(row: Payment) {
    setError("");
    setRefundFor(row);
    setAmount(row.available_for_refund ?? "");
    setReason("");
  }
  function openReject(row: Payment) {
    setError("");
    setRejectFor(row);
    setRejectReason("");
  }
  function openRestore(row: Payment) {
    setError("");
    setRestoreFor(row);
  }
  function openStatus(row: Payment) {
    setStatusFor(row);
  }
  function closeStatus() {
    setStatusFor(null);
  }

  return {
    page,
    query,
    setQuery,
    statusFilter,
    setStatusFilter,
    department,
    setDepartment,
    data,
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
    setError,
    receipt,
    issue,
    refund,
    reject,
    restore,
    refundFor,
    setRefundFor,
    statusFor,
    rejectFor,
    setRejectFor,
    restoreFor,
    setRestoreFor,
    qrFor,
    setQrFor,
    rejectReason,
    setRejectReason,
    amount,
    setAmount,
    reason,
    setReason,
    openRefund,
    openReject,
    openRestore,
    openStatus,
    closeStatus,
  };
}

export type Transactions = ReturnType<typeof useTransactions>;
