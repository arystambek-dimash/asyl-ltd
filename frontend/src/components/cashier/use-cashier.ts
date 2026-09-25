"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ClientDebt, Department, Me, Store } from "@/lib/types";
import { incomeTotals } from "@/lib/report-analytics";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { periodRange } from "@/lib/date-range";
import { apiUrl } from "@/lib/utils";
import {
  EMPTY_CASH_FILTERS,
  filterScreenFor,
  filtersAreValid,
  initialFilters,
  scopeParams,
  type CashFilters,
  type CashFiltersByScreen,
} from "./filters";
import {
  ALL_DEPARTMENTS,
  cashierName,
  departmentScope,
  queueDepartment,
  readStoredDepartment,
  scopeLabel,
  storeDepartment,
} from "./scope";
import { debtTotals, queueTotals, type AwaitingTotal, type IncomeSummary, type QueueTotal } from "./totals";
import { useCashierQueue } from "./use-cashier-queue";
import type { CashView, CashierPerms } from "./view";

/** Фильтры экрана с отделом кассы; null — отдел не навязывается. */
function withDepartment(filters: CashFilters, department: string | null): CashFilters {
  return department === null ? filters : { ...filters, department };
}

/**
 * Данные кассы для обеих раскладок. Активные запросы зависят от экрана:
 * «Общее» (десктоп) — сводка/долги/итоги оплат к подтверждению по своим фильтрам;
 * главная (телефон) — те же три запроса без фильтров (сводка строго за сегодня)
 * и итоги «Ждут оплаты»; отчёт и долги на телефоне — свои фильтры; «Оплаты» — одинаково везде.
 * POS — список должников без фильтров для поиска клиента.
 * На телефоне отдел для всех экранов задаёт переключатель в шапке, кроме оплат
 * к подтверждению: эта очередь общая для всех отделов (см. queueDepartment).
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
  const departmentAccess = departmentScope(me);
  const { assigned } = departmentAccess;
  const userId = me?.id;
  const [chosen, setChosen] = useState(() => readStoredDepartment(userId) ?? ALL_DEPARTMENTS);
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
  // Оплаты к подтверждению — общая очередь: закреплённый отдел её не сужает.
  const queueScopeDepartment = mobile ? queueDepartment(departmentAccess, chosen) : null;
  // Экранные фильтры с отделом из шапки; на десктопе отдел остаётся в панели фильтров.
  const scoped = useMemo<CashFiltersByScreen>(
    () => ({
      overview: filtersByScreen.overview,
      report: withDepartment(filtersByScreen.report, scopeDepartment),
      debts: withDepartment(filtersByScreen.debts, scopeDepartment),
      confirm: withDepartment(filtersByScreen.confirm, queueScopeDepartment),
    }),
    [filtersByScreen, queueScopeDepartment, scopeDepartment],
  );
  // «Ждут оплаты» — заказы отдела кассы, как долги: закреплённый отдел или отдел из шапки;
  // при «Все отделы» в шапке отдел выбирают быстрые фильтры экрана.
  const awaitingDepartment = mobile && department !== ALL_DEPARTMENTS ? department : null;
  const awaitingFilters = useMemo(
    () => withDepartment(filtersByScreen.confirm, awaitingDepartment),
    [filtersByScreen.confirm, awaitingDepartment],
  );
  const scopedEmpty = useMemo(() => withDepartment(EMPTY_CASH_FILTERS, scopeDepartment), [scopeDepartment]);
  const queueEmpty = useMemo(() => withDepartment(EMPTY_CASH_FILTERS, queueScopeDepartment), [queueScopeDepartment]);

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
  const queueSummaryFilters = overviewActive ? scoped.overview : homeActive ? queueEmpty : null;

  const summaryUrl =
    perms.canReports && summaryFilters && filtersAreValid(summaryFilters)
      ? apiUrl("/reports/summary/", {
          section: "income",
          date_from: summaryFilters.dateFrom,
          date_to: summaryFilters.dateTo,
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
  // Главной нужны только итоги заказов, которые ждут оплаты; сами заказы грузит экран «Оплаты».
  const awaitingSummary = useApi<AwaitingTotal[]>(
    homeActive && perms.canPayments
      ? apiUrl("/orders/awaiting-payment/", { summary: "1", ...scopeParams(scopedEmpty) })
      : null,
  );
  const { data: stores } = useApi<Store[]>(perms.canReports && perms.canViewStores ? "/stores/" : null);

  const { reload: reloadSummary } = summary;
  const { reload: reloadDebts } = debts;
  const { reload: reloadQueueSummary } = queueSummary;
  const { reload: reloadAwaitingSummary } = awaitingSummary;
  const reloadOverview = useCallback(async () => {
    // На десктопе итоги «Ждут оплаты» — хук с null-URL: перезагружать его незачем.
    const tasks = [reloadSummary(), reloadDebts(), reloadQueueSummary()];
    if (homeActive) tasks.push(reloadAwaitingSummary());
    await Promise.all(tasks);
  }, [homeActive, reloadAwaitingSummary, reloadDebts, reloadQueueSummary, reloadSummary]);

  const queue = useCashierQueue(
    perms.canPayments && view === "confirm",
    scoped.confirm,
    awaitingFilters,
    // «К возврату» — в кассе на компьютере; на телефоне этого списка нет.
    { refunds: !mobile },
  );

  const overviewValid = !overviewActive || filtersAreValid(filtersByScreen.overview);
  useVisiblePolling(reloadOverview, 30_000, (overviewActive || homeActive) && overviewValid);
  // Preserve rows the cashier explicitly expanded; manual refresh and
  // completed actions still reload the queue from its first page.
  useVisiblePolling(
    queue.reload,
    30_000,
    perms.canPayments && view === "confirm" && !queue.busy && !queue.loadingMore && queue.longestList <= 50,
  );

  const debtRows = debts.data ?? [];
  return {
    me,
    perms,
    view,
    filters,
    filterScreen,
    filtersByScreen,
    patchFilters,
    resetFilters,
    summary,
    debts,
    queueSummary,
    queue,
    stores: stores ?? [],
    departments: departments ?? [],
    /** Отдел кассы для шапки на телефоне: закреплённый или выбранный, с именем кассира. */
    scope: {
      assigned,
      department,
      setDepartment,
      cashier: cashierName(me),
      ...scopeLabel(department, departments ?? [], assigned),
      /** Подпись оплат к подтверждению: у закреплённого кассира — все отделы. */
      queueName: scopeLabel(queueScopeDepartment ?? ALL_DEPARTMENTS, departments ?? [], null).name,
    },
    income: incomeTotals(summary.data),
    incomeReady: summary.data !== null && !summary.error,
    queueTotals: queueTotals(queueSummary.data ?? []),
    queueReady: queueSummary.data !== null && !queueSummary.error,
    awaitingTotals: queueTotals(awaitingSummary.data ?? []),
    awaitingReady: awaitingSummary.data !== null && !awaitingSummary.error,
    debtRows,
    debtTotals: debtTotals(debtRows),
    debtsReady: debts.data !== null && !debts.error,
    reloadOverview,
  };
}

export type CashierModel = ReturnType<typeof useCashier>;
