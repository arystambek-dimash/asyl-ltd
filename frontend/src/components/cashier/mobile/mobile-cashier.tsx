"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { cn } from "@/lib/utils";
import { CashFiltersSheet, FilterButton } from "../cash-filters-sheet";
import { activeFilterCount, filtersError } from "../filters";
import { ALL_DEPARTMENTS } from "../scope";
import type { CashierModel } from "../use-cashier";
import { cashierTabs, hasHomeScreen, mobileMenu, type CashTabKey, type CashView } from "../view";
import { ConfirmScreen } from "./confirm-screen";
import { DebtsScreen } from "./debts-screen";
import { DepartmentSheet } from "./department-sheet";
import { HomeScreen, type HomeItemKey } from "./home-screen";
import { JournalScreen } from "./journal-screen";
import { PosScreen } from "./pos/pos-screen";
import { usePosFlow } from "./pos/use-pos-flow";
import { ReportScreen } from "./report-screen";
import { CashierTabBar } from "./tab-bar";
import { TransactionsScreen } from "./transactions-screen";

export const SCREEN_TITLES: Record<CashView, string> = {
  home: "Касса",
  overview: "Касса",
  report: "Отчёт по поступлениям",
  debts: "Долги клиентов",
  confirm: "Заявки и оплаты",
  journal: "Журнал",
  transactions: "Транзакции",
  pos: "POS",
  remote: "Удаленная оплата",
};

/** Шапка главной: отдел — «касса», как название точки в Kaspi; с правом на все отделы — кнопка выбора. */
function DepartmentHeading({ scope, onOpen }: { scope: CashierModel["scope"]; onOpen: () => void }) {
  const dot = (
    <span
      aria-hidden
      className={cn("size-2.5 shrink-0 rounded-full", !scope.color && "bg-[var(--muted-foreground)]")}
      style={scope.color ? { backgroundColor: scope.color } : undefined}
    />
  );
  if (!scope.switchable) {
    return (
      <span className="flex items-center gap-2">
        {dot}
        <span className="truncate">{scope.name}</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      title="Сменить отдел"
      className="-mx-1 flex max-w-full items-center gap-2 rounded-md px-1 transition-colors hover:bg-[var(--secondary)]"
    >
      {dot}
      <span className="truncate">{scope.name}</span>
      <ChevronDown className="size-4 shrink-0 text-[var(--muted-foreground)]" aria-hidden />
    </button>
  );
}

/** Касса на телефоне: главная-меню, подэкраны с «‹ назад» и нижняя панель как в Kaspi. */
export function MobileCashier({ model }: { model: CashierModel }) {
  const router = useRouter();
  const pathname = usePathname();
  const { view, perms, filterScreen, scope, departments, debts } = model;
  const menu = mobileMenu(perms);
  const tabs = cashierTabs(perms);
  // «‹» возвращает историей, только если экран открыт отсюда; по диплинку — заменяем адрес на главную.
  const cameFromHome = useRef(false);
  useEffect(() => {
    if (view === "home") cameFromHome.current = false;
  }, [view]);
  const open = useCallback(
    (next: HomeItemKey) => {
      cameFromHome.current = true;
      router.push(`${pathname}?view=${next}`);
    },
    [pathname, router],
  );
  const back = useCallback(() => {
    if (cameFromHome.current) router.back();
    else router.replace(pathname);
  }, [pathname, router]);
  // Нижняя панель переключает разделы без накопления истории: «‹» с любого из них ведёт на главную.
  const go = useCallback(
    (tab: CashTabKey) => {
      cameFromHome.current = false;
      router.replace(tab === "home" ? pathname : `${pathname}?view=${tab}`);
    },
    [pathname, router],
  );

  // Состояние POS живёт здесь, а не в его экране: панель внизу видна и во время оплаты,
  // а начатая оплата переживает переход в «Историю» и обратно.
  const posView = view === "pos" || view === "remote" ? view : null;
  const { reload: reloadDebts } = debts;
  const onPaid = useCallback(() => void reloadDebts(), [reloadDebts]);
  const pos = usePosFlow({
    flow: posView ? (posView === "remote" ? "remote" : "qr") : null,
    onPaid,
    department: scope.department !== ALL_DEPARTMENTS ? scope.department : null,
  });

  const [filtersOpen, setFiltersOpen] = useState(false);
  const [departmentOpen, setDepartmentOpen] = useState(false);
  // Открытая шторка не должна пережить переход на другой подэкран.
  useEffect(() => {
    setFiltersOpen(false);
    setDepartmentOpen(false);
  }, [view]);
  const showBack = view !== "home" && hasHomeScreen(perms);
  const activeFilters = filterScreen
    ? activeFilterCount(model.filters, {
        dates: filterScreen !== "report",
        remaining: filterScreen === "debts",
        department: false,
      })
    : 0;
  const rangeError = filterScreen ? filtersError(model.filters) : null;

  const activeTab: CashTabKey | null =
    view === "home" ? "home" : tabs.includes(view as CashTabKey) ? (view as CashTabKey) : null;
  const bar =
    tabs.length > 0 ? <CashierTabBar tabs={tabs} active={activeTab} disabled={pos.busy} onSelect={go} /> : null;

  if (posView) {
    return (
      <PosScreen
        model={model}
        flow={pos}
        title={SCREEN_TITLES[posView]}
        section={scope.name}
        footer={bar}
        onClose={back}
        onOpenHistory={perms.canTransactions ? () => go("transactions") : undefined}
      />
    );
  }

  return (
    <AppShell
      title={
        view === "home" ? (
          <DepartmentHeading scope={scope} onOpen={() => setDepartmentOpen(true)} />
        ) : (
          SCREEN_TITLES[view]
        )
      }
      section={view === "home" ? scope.cashier || "Касса" : scope.name}
      back={showBack ? { label: "Назад в кассу", onClick: back } : undefined}
      trailing={filterScreen ? <FilterButton count={activeFilters} onClick={() => setFiltersOpen(true)} /> : undefined}
    >
      {rangeError && <p className="mb-3 text-xs font-medium text-[var(--destructive)]">{rangeError}</p>}
      {filterScreen && (
        <CashFiltersSheet
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          filters={model.filters}
          stores={model.stores}
          departments={departments}
          showRemaining={filterScreen === "debts"}
          showDates={filterScreen !== "report"}
          onChange={model.patchFilters}
        />
      )}
      {scope.switchable && (
        <DepartmentSheet
          open={departmentOpen}
          onClose={() => setDepartmentOpen(false)}
          departments={departments}
          selected={scope.department}
          onSelect={scope.setDepartment}
        />
      )}
      {view === "home" && <HomeScreen model={model} menu={menu} onOpen={open} />}
      {view === "confirm" && <ConfirmScreen model={model} />}
      {view === "journal" && <JournalScreen model={model} />}
      {view === "debts" && <DebtsScreen model={model} />}
      {view === "report" && <ReportScreen model={model} />}
      {view === "transactions" && <TransactionsScreen model={model} />}
      {bar && (
        <>
          {/* Место под панель, чтобы она не закрывала последнюю строку списка. */}
          <div aria-hidden className="h-20" />
          {bar}
        </>
      )}
    </AppShell>
  );
}
