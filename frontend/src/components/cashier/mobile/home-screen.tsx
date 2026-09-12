"use client";
import { ChartPie, ChevronRight, HandCoins, History, Receipt, Users } from "lucide-react";
import { ErrorAlert } from "@/components/ui/data-state";
import { NavList } from "@/components/ui/nav-list";
import { cn, formatCompactCurrency, formatCurrency, pluralRu } from "@/lib/utils";
import type { CashierModel } from "../use-cashier";
import type { MobileMenuKey } from "../view";

const ITEMS: Record<MobileMenuKey, { title: string; icon: React.ElementType; hint: string }> = {
  confirm: { title: "Заявки и оплаты", icon: HandCoins, hint: "Очередь подтверждения" },
  debts: { title: "Долги клиентов", icon: Users, hint: "Остатки по клиентам" },
  transactions: { title: "Транзакции", icon: Receipt, hint: "Все платежи, возвраты и чеки" },
  journal: { title: "Журнал", icon: History, hint: "Действия по оплатам" },
  report: { title: "Отчёт по поступлениям", icon: ChartPie, hint: "По отделам и способам оплаты" },
};

function confirmSubtitle(model: CashierModel): string {
  if (!model.queueReady) return ITEMS.confirm.hint;
  const parts: string[] = [];
  const pending = model.pendingCount;
  if (model.perms.canReviewOrders && !pending.error && pending.count > 0) {
    parts.push(`${pending.count} ${pluralRu(pending.count, ["заявка", "заявки", "заявок"])}`);
  }
  const { count, total, currency } = model.queueTotals;
  if (count > 0) {
    parts.push(
      `${count} ${pluralRu(count, ["оплата", "оплаты", "оплат"])} на ${formatCompactCurrency(total, currency)}`,
    );
  }
  return parts.length ? parts.join(" · ") : "Очередь пуста";
}

function debtsSubtitle(model: CashierModel): string {
  if (!model.debtsReady) return ITEMS.debts.hint;
  const { clients, total, currency, other, overdue } = model.debtTotals;
  if (clients === 0) return "Долгов нет";
  // Валюты не складываются: «1,2 млн ₸ + 500 $».
  const amount = [
    formatCompactCurrency(total, currency),
    ...other.map(([unit, value]) => formatCompactCurrency(value, unit)),
  ].join(" + ");
  return [
    `${clients} ${pluralRu(clients, ["клиент", "клиента", "клиентов"])}`,
    amount,
    overdue > 0 ? `${overdue} с просрочкой` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Главная кассы на телефоне: карточка-сводка сверху и список разделов с живыми цифрами. */
export function HomeScreen({
  model,
  menu,
  onOpen,
}: {
  model: CashierModel;
  menu: MobileMenuKey[];
  onOpen: (view: MobileMenuKey) => void;
}) {
  const { perms, income, incomeReady, queueTotals, queueReady, summary, queueSummary, debts } = model;
  const headline = perms.canReports
    ? {
        title: "Поступления за сегодня",
        caption: "чистыми, с учётом возвратов",
        value: incomeReady ? formatCurrency(income.total, income.currency) : "—",
        negative: incomeReady && income.total < 0,
        target: "report" as const,
      }
    : perms.canPayments
      ? {
          title: "Ожидает подтверждения",
          caption: queueReady
            ? `${queueTotals.count} ${pluralRu(queueTotals.count, ["оплата", "оплаты", "оплат"])} в очереди`
            : "",
          value: queueReady ? formatCurrency(queueTotals.total, queueTotals.currency) : "—",
          negative: false,
          target: "confirm" as const,
        }
      : null;
  const subtitles: Record<MobileMenuKey, string> = {
    confirm: confirmSubtitle(model),
    debts: debtsSubtitle(model),
    transactions: ITEMS.transactions.hint,
    journal: ITEMS.journal.hint,
    report: ITEMS.report.hint,
  };
  const loadError = summary.error || queueSummary.error || debts.error;

  return (
    <div className="flex flex-col gap-4">
      {headline && (
        <button
          type="button"
          onClick={() => onOpen(headline.target)}
          className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-3.5 text-left shadow-card transition-colors hover:bg-[var(--muted)]/60"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium">{headline.title}</span>
            {headline.caption && (
              <span className="block text-[13px] text-[var(--muted-foreground)]">{headline.caption}</span>
            )}
          </span>
          <span className={cn("text-[17px] font-bold tabular-nums", headline.negative && "text-[var(--destructive)]")}>
            {headline.value}
          </span>
          <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
        </button>
      )}
      {loadError && <ErrorAlert message={loadError} onRetry={model.reloadOverview} />}
      <NavList
        label="Разделы кассы"
        items={menu.map((key) => ({
          key,
          icon: ITEMS[key].icon,
          title: ITEMS[key].title,
          subtitle: subtitles[key],
          onSelect: () => onOpen(key),
        }))}
      />
    </div>
  );
}
