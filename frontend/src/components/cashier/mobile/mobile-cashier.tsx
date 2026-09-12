"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { CashFiltersSheet, FilterButton } from "../cash-filters-sheet";
import { activeFilterCount, filtersError } from "../filters";
import type { CashierModel } from "../use-cashier";
import { mobileMenu, type CashView, type MobileMenuKey } from "../view";
import { ConfirmScreen } from "./confirm-screen";
import { DebtsScreen } from "./debts-screen";
import { HomeScreen } from "./home-screen";
import { JournalScreen } from "./journal-screen";
import { PosFab } from "./pos/pos-fab";
import { PosScreen } from "./pos/pos-screen";
import { ReportScreen } from "./report-screen";
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
};

/** Касса на телефоне: главная-меню и подэкраны с «‹ назад» вместо «☰». */
export function MobileCashier({ model }: { model: CashierModel }) {
  const router = useRouter();
  const pathname = usePathname();
  const { view, perms, filterScreen } = model;
  const menu = mobileMenu(perms);
  // «‹» возвращает историей, только если экран открыт отсюда; по диплинку — заменяем адрес на главную.
  const cameFromHome = useRef(false);
  useEffect(() => {
    if (view === "home") cameFromHome.current = false;
  }, [view]);
  const open = useCallback(
    (next: MobileMenuKey | "pos") => {
      cameFromHome.current = true;
      router.push(`${pathname}?view=${next}`);
    },
    [pathname, router],
  );
  const back = useCallback(() => {
    if (cameFromHome.current) router.back();
    else router.replace(pathname);
  }, [pathname, router]);

  const [filtersOpen, setFiltersOpen] = useState(false);
  // Открытая шторка не должна пережить переход на другой подэкран.
  useEffect(() => setFiltersOpen(false), [view]);
  const showBack = view !== "home" && menu.length > 1;
  const activeFilters = filterScreen
    ? activeFilterCount(model.filters, { dates: filterScreen !== "report", remaining: filterScreen === "debts" })
    : 0;
  const rangeError = filterScreen ? filtersError(model.filters) : null;

  // POS рисует свой топбар и нижнюю панель; «‹» на первом шаге закрывает его, как подэкран.
  if (view === "pos") return <PosScreen model={model} onClose={back} />;
  const showPos = perms.canCreatePayments;

  return (
    <AppShell
      title={SCREEN_TITLES[view]}
      section={view === "home" ? "Работа" : "Касса"}
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
          departments={model.departments}
          showRemaining={filterScreen === "debts"}
          showDates={filterScreen !== "report"}
          onChange={model.patchFilters}
        />
      )}
      {view === "home" && <HomeScreen model={model} menu={menu} onOpen={open} />}
      {view === "confirm" && <ConfirmScreen model={model} />}
      {view === "journal" && <JournalScreen model={model} />}
      {view === "debts" && <DebtsScreen model={model} />}
      {view === "report" && <ReportScreen model={model} />}
      {view === "transactions" && <TransactionsScreen model={model} />}
      {showPos && (
        <>
          {/* Место под кнопку POS, чтобы она не закрывала последнюю строку списка. */}
          <div aria-hidden className="h-20" />
          <PosFab onClick={() => open("pos")} />
        </>
      )}
    </AppShell>
  );
}
