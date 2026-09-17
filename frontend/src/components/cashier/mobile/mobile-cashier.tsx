"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown, QrCode } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { cn } from "@/lib/utils";
import { CashFiltersSheet, FilterButton } from "../cash-filters-sheet";
import { activeFilterCount, filtersError } from "../filters";
import { ALL_DEPARTMENTS } from "../scope";
import type { CashierModel } from "../use-cashier";
import { hasHomeScreen, mobileMenu, type CashView, type MobileMenuKey } from "../view";
import { BottomBar, type BottomBarItem } from "./bottom-bar";
import { ConfirmScreen } from "./confirm-screen";
import { DebtsScreen } from "./debts-screen";
import { DepartmentSheet } from "./department-sheet";
import { HomeScreen } from "./home-screen";
import { JournalScreen } from "./journal-screen";
import { PosScreen } from "./pos/pos-screen";
import { usePosFlow } from "./pos/use-pos-flow";
import { ReportScreen } from "./report-screen";
import { TransactionsScreen } from "./transactions-screen";

/** Панель внизу кассы — как навигация телефона: один пункт, POS. */
const POS_BAR: BottomBarItem<"pos">[] = [{ key: "pos", label: "POS", icon: QrCode, accent: true }];

/** Заголовки экранов кассы; POS называет себя сам по вкладке. */
export const SCREEN_TITLES: Record<Exclude<CashView, "pos" | "remote">, string> = {
  home: "Касса",
  overview: "Касса",
  report: "Отчёт по поступлениям",
  debts: "Долги клиентов",
  confirm: "Оплаты",
  journal: "Журнал",
  transactions: "Транзакции",
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
  // «‹» возвращает историей, только если экран открыт отсюда; по диплинку — заменяем адрес на главную.
  const cameFromHome = useRef(false);
  useEffect(() => {
    if (view === "home") cameFromHome.current = false;
  }, [view]);
  const open = useCallback(
    (next: MobileMenuKey) => {
      cameFromHome.current = true;
      router.push(`${pathname}?view=${next}`);
    },
    [pathname, router],
  );
  const back = useCallback(() => {
    if (cameFromHome.current) router.back();
    else router.replace(pathname);
  }, [pathname, router]);
  // Кнопка POS в панели: с главной — с историей (аппаратный «назад» вернёт домой, начатая
  // оплата останется), с подэкрана — заменой адреса, чтобы «‹» вёл на главную, а не на подэкран.
  // Флаг cameFromHome при замене не трогаем: под заменённой записью по-прежнему главная (или нет).
  const openPos = useCallback(() => {
    if (view === "home") {
      cameFromHome.current = true;
      router.push(`${pathname}?view=pos`);
    } else {
      router.replace(`${pathname}?view=pos`);
    }
  }, [pathname, router, view]);

  // Состояние POS живёт здесь, а не в его экране: начатая оплата переживает выход на главную и обратно.
  const posView = view === "pos" || view === "remote" ? view : null;
  const { reload: reloadDebts } = debts;
  const onPaid = useCallback(() => void reloadDebts(), [reloadDebts]);
  const pos = usePosFlow({
    entry: posView ? (posView === "remote" ? "remote" : "qr") : null,
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

  if (posView) return <PosScreen model={model} flow={pos} section={scope.name} onClose={back} />;
  // После раннего возврата view — не POS; TypeScript этого не выводит.
  const screen = view as Exclude<CashView, "pos" | "remote">;

  return (
    <AppShell
      title={
        screen === "home" ? (
          <DepartmentHeading scope={scope} onOpen={() => setDepartmentOpen(true)} />
        ) : (
          SCREEN_TITLES[screen]
        )
      }
      section={view === "home" ? scope.cashier || "Касса" : view === "confirm" ? scope.queueName : scope.name}
      back={showBack ? { label: "Назад в кассу", onClick: back } : undefined}
      trailing={filterScreen ? <FilterButton count={activeFilters} onClick={() => setFiltersOpen(true)} /> : undefined}
      footer={
        perms.canCreatePayments ? (
          <BottomBar label="Панель кассы" items={POS_BAR} active={null} disabled={pos.busy} onSelect={openPos} />
        ) : undefined
      }
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
    </AppShell>
  );
}
