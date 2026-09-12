"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CashierLogItem, ClientDebt, Department, Me, Order, Store } from "@/lib/types";
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
import {
  ALL_DEPARTMENTS,
  cashierName,
  departmentScope,
  readStoredDepartment,
  scopeLabel,
  storeDepartment,
} from "./scope";
import { debtTotals, incomeTotals, queueTotals, type IncomeSummary, type QueueTotal } from "./totals";
import { useCashierQueue } from "./use-cashier-queue";
import type { CashView, CashierPerms } from "./view";

/**
 * Данные кассы для обеих раскладок. Активные запросы зависят от экрана:
 * «Общее» (десктоп) — сводка/долги/очередь по своим фильтрам; главная
 * (телефон) — те же три запроса без фильтров, сводка строго за сегодня;
 * отчёт и долги на телефоне — свои фильтры; очередь и журнал — одинаково везде.
 * POS — список должников без фильтров для поиска клиента.
 * На телефоне отдел для всех экранов задаёт переключатель в шапке.
 */
export function useCashier({
  view,
  mobile,
  perms,
  me,
}: {
  view: CashView;
  mobile: boolean;
  perms: CashierPerms;
  me: Me | null;
}) {
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

  // Отдел кассы: закреплённый в карточке сотрудника или выбранный в шапке (запоминается на устройстве
  // отдельно для каждого пользователя — телефон у кассиров может быть общий).
  const { assigned, switchable } = departmentScope(me);
  const userId = me?.id;
  const [chosen, setChosen] = useState(
    () => readStoredDepartment(userId) ?? me?.sales_department?.code ?? ALL_DEPARTMENTS,
  );
  const setDepartment = useCallback(
    (code: string) => {
      setChosen(code);
      storeDepartment(code, userId);
    },
    [userId],
  );
  const { data: departments } = useApi<Department[]>("/departments/");
  // Отключённый отдел не должен оставить пустую кассу — и не должен возвращаться при следующем входе.
  useEffect(() => {
    if (departments && chosen !== ALL_DEPARTMENTS && !departments.some((row) => row.code === chosen)) {
      setDepartment(ALL_DEPARTMENTS);
    }
  }, [chosen, departments, setDepartment]);
  // Касса на телефоне работает по одному отделу: закреплённому или выбранному в шапке.
  const department = assigned ? assigned.code : chosen;
  const scopeDepartment = mobile ? department : null;
  // Экранные фильтры с отделом из шапки; на десктопе отдел остаётся в панели фильтров.
  const scoped = useMemo<CashFiltersByScreen>(() => {
    if (scopeDepartment === null) return filtersByScreen;
    const withDepartment = (screen: CashFilters): CashFilters => ({ ...screen, department: scopeDepartment });
    return {
      overview: filtersByScreen.overview,
      report: withDepartment(filtersByScreen.report),
      debts: withDepartment(filtersByScreen.debts),
      confirm: withDepartment(filtersByScreen.confirm),
      journal: withDepartment(filtersByScreen.journal),
    };
  }, [filtersByScreen, scopeDepartment]);
  const scopedEmpty = useMemo<CashFilters>(
    () => (scopeDepartment === null ? EMPTY_CASH_FILTERS : { ...EMPTY_CASH_FILTERS, department: scopeDepartment }),
    [scopeDepartment],
  );

  const overviewActive = !mobile && view === "overview";
  const homeActive = mobile && view === "home";
  const reportActive = mobile && view === "report";
  const debtsActive = mobile && view === "debts";
  const posActive = mobile && (view === "pos" || view === "remote");

  const summaryFilters = overviewActive
    ? scoped.overview
    : reportActive
      ? scoped.report
      : homeActive
        ? { ...scopedEmpty, ...periodRange("today") }
        : null;
  const debtsFilters = overviewActive
    ? scoped.overview
    : debtsActive
      ? scoped.debts
      : homeActive || posActive
        ? scopedEmpty
        : null;
  const queueSummaryFilters = overviewActive ? scoped.overview : homeActive ? scopedEmpty : null;

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
  // Главной нужно только число заявок (по отделу кассы, как и очередь); сами заявки грузит экран очереди.
  const pendingCount = usePagedApi<Order>(
    homeActive && perms.canReviewOrders
      ? apiUrl("/orders/", { ...scopeParams(scopedEmpty), status_group: "pending" })
      : null,
    1,
  );
  const journalFilters = scoped.journal;
  const journalLog = usePagedApi<CashierLogItem>(
    perms.canPayments && view === "journal" && filtersAreValid(journalFilters)
      ? apiUrl("/orders/cashier-log/", scopeParams(journalFilters))
      : null,
    50,
  );
  const { data: stores } = useApi<Store[]>(perms.canReports && perms.canViewClients ? "/stores/" : null);

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
    scoped.confirm,
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
    /** Отдел кассы для шапки на телефоне: закреплённый или выбранный, с именем кассира. */
    scope: {
      assigned,
      switchable,
      department,
      setDepartment,
      cashier: cashierName(me),
      ...scopeLabel(department, departments ?? [], assigned),
    },
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
