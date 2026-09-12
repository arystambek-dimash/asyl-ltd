"use client";
import { useCallback, useState } from "react";
import type { CashierLogItem, ClientDebt, Department, Order, Store } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import {
  EMPTY_CASH_FILTERS,
  apiUrl,
  filterScreenFor,
  filtersAreValid,
  initialFilters,
  periodRange,
  scopeParams,
  type CashFilters,
  type CashFiltersByScreen,
} from "./filters";
import { debtTotals, incomeTotals, queueTotals, type IncomeSummary, type QueueTotal } from "./totals";
import { useCashierQueue } from "./use-cashier-queue";
import type { CashView, CashierPerms } from "./view";

/**
 * Данные кассы для обеих раскладок. Активные запросы зависят от экрана:
 * «Общее» (десктоп) — сводка/долги/очередь по своим фильтрам; главная
 * (телефон) — те же три запроса без фильтров, сводка строго за сегодня;
 * отчёт и долги на телефоне — свои фильтры; очередь и журнал — одинаково везде.
 */
export function useCashier({ view, mobile, perms }: { view: CashView; mobile: boolean; perms: CashierPerms }) {
  const [filtersByScreen, setFiltersByScreen] = useState<CashFiltersByScreen>(initialFilters);
  const filterScreen = filterScreenFor(view, mobile);
  const filters = filterScreen ? filtersByScreen[filterScreen] : EMPTY_CASH_FILTERS;

  const patchFilters = useCallback(
    (patch: Partial<CashFilters>) => {
      if (!filterScreen) return;
      setFiltersByScreen((current) => ({ ...current, [filterScreen]: { ...current[filterScreen], ...patch } }));
    },
    [filterScreen],
  );
  const resetFilters = useCallback(() => {
    if (!filterScreen) return;
    setFiltersByScreen((current) => ({ ...current, [filterScreen]: EMPTY_CASH_FILTERS }));
  }, [filterScreen]);

  const overviewActive = !mobile && view === "overview";
  const homeActive = mobile && view === "home";
  const reportActive = mobile && view === "report";
  const debtsActive = mobile && view === "debts";

  const summaryFilters = overviewActive
    ? filtersByScreen.overview
    : reportActive
      ? filtersByScreen.report
      : homeActive
        ? { ...EMPTY_CASH_FILTERS, ...periodRange("today") }
        : null;
  const debtsFilters = overviewActive
    ? filtersByScreen.overview
    : debtsActive
      ? filtersByScreen.debts
      : homeActive
        ? EMPTY_CASH_FILTERS
        : null;
  const queueSummaryFilters = overviewActive ? filtersByScreen.overview : homeActive ? EMPTY_CASH_FILTERS : null;

  const summaryUrl =
    perms.canReports && summaryFilters && filtersAreValid(summaryFilters)
      ? apiUrl("/reports/summary/", {
          section: "income",
          from: summaryFilters.dateFrom,
          to: summaryFilters.dateTo,
          department: summaryFilters.department,
          store: summaryFilters.store,
        })
      : null;
  const debtsUrl =
    perms.canDebtEntry && debtsFilters && filtersAreValid(debtsFilters)
      ? apiUrl("/clients/debts/", {
          ...scopeParams(debtsFilters),
          remaining_min: debtsFilters.remainingMin,
          remaining_max: debtsFilters.remainingMax,
          remaining_currency: debtsFilters.remainingCurrency,
        })
      : null;
  const queueSummaryUrl =
    perms.canPayments && queueSummaryFilters && filtersAreValid(queueSummaryFilters)
      ? apiUrl("/orders/payments-queue/", { summary: "1", ...scopeParams(queueSummaryFilters) })
      : null;

  // Кассовая аналитика — тот же серверный отчёт, что и на «Отчётах».
  const summary = useApi<IncomeSummary>(summaryUrl);
  const debts = useApi<ClientDebt[]>(debtsUrl);
  const queueSummary = useApi<QueueTotal[]>(queueSummaryUrl);
  // Главной нужно только число заявок; сами заявки грузит экран очереди.
  const pendingCount = usePagedApi<Order>(
    homeActive && perms.canReviewOrders ? "/orders/?status_group=pending" : null,
    1,
  );
  const journalFilters = filtersByScreen.journal;
  const journalLog = usePagedApi<CashierLogItem>(
    perms.canPayments && view === "journal" && filtersAreValid(journalFilters)
      ? apiUrl("/orders/cashier-log/", scopeParams(journalFilters))
      : null,
    50,
  );
  const { data: stores } = useApi<Store[]>(perms.canReports && perms.canViewClients ? "/stores/" : null);
  const { data: departments } = useApi<Department[]>("/departments/");

  const { reload: reloadSummary } = summary;
  const { reload: reloadDebts } = debts;
  const { reload: reloadQueueSummary } = queueSummary;
  const { reload: reloadPendingCount } = pendingCount;
  const { reload: reloadJournal } = journalLog;
  const reloadOverview = useCallback(async () => {
    // На десктопе pendingCount — хук с null-URL: перезагружать его незачем.
    const tasks = [reloadSummary(), reloadDebts(), reloadQueueSummary()];
    if (homeActive) tasks.push(reloadPendingCount());
    await Promise.all(tasks);
  }, [homeActive, reloadDebts, reloadPendingCount, reloadQueueSummary, reloadSummary]);
  const paymentChanged = useCallback(async () => {
    await Promise.all([reloadOverview(), reloadJournal()]);
  }, [reloadJournal, reloadOverview]);

  const queue = useCashierQueue(
    perms.canPayments && view === "confirm",
    perms.canReviewOrders,
    filtersByScreen.confirm,
    paymentChanged,
  );

  const overviewValid = !overviewActive || filtersAreValid(filtersByScreen.overview);
  useVisiblePolling(reloadOverview, 30_000, (overviewActive || homeActive) && overviewValid && !queue.busy);
  // Preserve rows the cashier explicitly expanded; manual refresh and
  // completed actions still reload the queue from its first page.
  useVisiblePolling(
    queue.refresh,
    30_000,
    perms.canPayments &&
      view === "confirm" &&
      !queue.busy &&
      !queue.pendingPage.loadingMore &&
      !queue.queuePage.loadingMore &&
      queue.pendingOrders.length <= 50 &&
      queue.toReview.length <= 50,
  );

  const debtRows = debts.data ?? [];
  return {
    perms,
    view,
    mobile,
    filters,
    filterScreen,
    filtersByScreen,
    patchFilters,
    resetFilters,
    summary,
    debts,
    queueSummary,
    pendingCount,
    journalLog,
    queue,
    stores: stores ?? [],
    departments: departments ?? [],
    income: incomeTotals(summary.data),
    incomeReady: summary.data !== null && !summary.error,
    queueTotals: queueTotals(queueSummary.data ?? []),
    queueReady: queueSummary.data !== null && !queueSummary.error,
    debtRows,
    debtTotals: debtTotals(debtRows),
    debtsReady: debts.data !== null && !debts.error,
    reloadOverview,
    paymentChanged,
  };
}

export type CashierModel = ReturnType<typeof useCashier>;
