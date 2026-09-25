import { can } from "@/lib/can";
import type { Me } from "@/lib/types";

/** Экран кассы: десктоп знает overview/confirm/transactions, телефон — home/report/debts/confirm/transactions/pos/remote. */
const CASH_VIEWS = ["home", "overview", "report", "debts", "confirm", "transactions", "pos", "remote"] as const;
export type CashView = (typeof CASH_VIEWS)[number];
export type MobileMenuKey = Exclude<CashView, "home" | "overview" | "pos" | "remote">;

/** Права раздела — RequirePerm пускает при любом из них. */
export const CASHIER_ENTRY_PERMS = ["payments.confirm", "payments.create", "reports.view", "payments.view"];

export interface CashierPerms {
  canPayments: boolean;
  canCreatePayments: boolean;
  canReports: boolean;
  canDebtEntry: boolean;
  canTransactions: boolean;
  canViewOrders: boolean;
  canViewStores: boolean;
  canCheckOverdue: boolean;
}

export function cashierPerms(me: Me | null): CashierPerms {
  const canPayments = can(me, "payments.confirm");
  const canCreatePayments = can(me, "payments.create");
  const canReports = can(me, "reports.view");
  const canViewOrders = can(me, "orders.view");
  return {
    canPayments,
    canCreatePayments,
    canReports,
    canDebtEntry: canReports || canCreatePayments,
    canTransactions: can(me, "payments.view"),
    canViewOrders,
    canViewStores: can(me, "stores.view"),
    canCheckOverdue: can(me, "stores.edit"),
  };
}

function viewAllowed(view: CashView, perms: CashierPerms): boolean {
  switch (view) {
    case "home":
      return true;
    case "overview":
    case "debts":
      return perms.canDebtEntry;
    case "report":
      return perms.canReports;
    case "confirm":
      return perms.canPayments;
    case "pos":
    case "remote":
      return perms.canCreatePayments;
    case "transactions":
      return perms.canTransactions;
  }
}

/** Порядок пунктов мобильного меню фиксированный — как в спеке. */
const MOBILE_MENU: MobileMenuKey[] = ["confirm", "debts", "transactions", "report"];
const DESKTOP_VIEWS: CashView[] = ["overview", "confirm", "transactions"];

export function mobileMenu(perms: CashierPerms): MobileMenuKey[] {
  return MOBILE_MENU.filter((key) => viewAllowed(key, perms));
}

/** Главная нужна, когда разделов больше одного; POS — тоже раздел, иначе роль «только оплаты» до него не доберётся. */
export function hasHomeScreen(perms: CashierPerms): boolean {
  return mobileMenu(perms).length + (perms.canCreatePayments ? 1 : 0) > 1;
}

function defaultView(perms: CashierPerms, mobile: boolean): CashView {
  if (mobile) {
    // Один доступный пункт — открываем его сразу, главная с одной строкой не нужна.
    const menu = mobileMenu(perms);
    return !hasHomeScreen(perms) && menu.length === 1 ? menu[0] : "home";
  }
  return DESKTOP_VIEWS.find((view) => viewAllowed(view, perms)) ?? "transactions";
}

/** Экран из `?view=`: чужие для раскладки значения сводятся по таблице спеки, недоступные — к экрану по умолчанию. */
export function resolveView(raw: string | null, perms: CashierPerms, mobile: boolean): CashView {
  const fallback = defaultView(perms, mobile);
  if (!raw || !(CASH_VIEWS as readonly string[]).includes(raw)) return fallback;
  let view = raw as CashView;
  if (mobile && view === "overview") view = "home";
  if (!mobile && (view === "home" || view === "report" || view === "debts" || view === "pos" || view === "remote")) {
    view = "overview";
  }
  if (view === "home") return fallback;
  return viewAllowed(view, perms) ? view : fallback;
}
