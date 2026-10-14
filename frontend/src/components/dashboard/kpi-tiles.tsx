import Link from "next/link";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import {
  cn,
  currencySymbol,
  formatCompact,
  formatCompactCurrency,
  formatCount,
  formatCurrency,
  formatTons,
  pluralRu,
} from "@/lib/utils";

type Tile = {
  key: string;
  label: string;
  value: string;
  exact: string;
  unit: string;
  /** Вес рядом с мешками, «7,1 т». */
  tons?: string;
  notes: string[];
  href: string;
};

/** Мешки на ТВ — точным числом; от миллиона — «14,9 млн», иначе число не влезает в плитку. */
function bagsValue(bags: number): string {
  return Math.abs(bags) >= 1_000_000 ? formatCompact(bags) : formatCount(bags);
}

function tiles(m: DashboardMetrics): Tile[] {
  const result: Tile[] = [];
  if (m.canOrders) {
    const yesterday = `вчера ${formatCount(m.shippedYesterday)}`;
    result.push({
      key: "shipped",
      label: "Отгружено сегодня",
      value: bagsValue(m.shippedToday),
      exact: formatCount(m.shippedToday),
      unit: "меш.",
      tons: `${formatTons(m.shippedTodayKg)} т`,
      notes:
        m.shippedTodayOrders > 0
          ? [`фуры ${formatCount(m.truckBagsToday)} · вагоны ${formatCount(m.trainBagsToday)}`, yesterday]
          : ["Пока не отгружали", yesterday],
      href: "/orders",
    });
  }
  if (m.canStock) {
    const positions = m.stockRows.length;
    result.push({
      key: "stock",
      label: "На складе",
      value: bagsValue(m.stockTotal),
      exact: formatCount(m.stockTotal),
      unit: "меш.",
      tons: `${formatTons(m.stockKg)} т`,
      notes: [`${m.stockWarehouse} · ${positions} ${pluralRu(positions, ["позиция", "позиции", "позиций"])}`],
      href: "/warehouse",
    });
  }
  if (m.canFinance) {
    const count = m.receivedTodayCount;
    result.push({
      key: "received",
      label: "Поступило сегодня",
      value: formatCompact(m.receivedToday),
      exact: formatCurrency(m.receivedToday, m.moneyCurrency),
      unit: currencySymbol(m.moneyCurrency),
      notes: [count > 0 ? `${count} ${pluralRu(count, ["оплата", "оплаты", "оплат"])}` : "Оплат пока нет"],
      href: "/accounting",
    });
    const others = m.debtOthers.map(([currency, value]) => `+ ${formatCompactCurrency(value, currency)}`);
    result.push({
      key: "debt",
      label: "Долг клиентов",
      value: formatCompact(m.debtTotal),
      exact: formatCurrency(m.debtTotal, m.debtCurrency),
      unit: currencySymbol(m.debtCurrency),
      notes: [
        m.overdueClients > 0
          ? `просрочено ${formatCompactCurrency(m.overdueTotal, m.overdueCurrency)} · ${m.overdueClients} ${pluralRu(m.overdueClients, ["клиент", "клиента", "клиентов"])}`
          : "Просрочки нет",
        ...(others.length ? [others.join(" ")] : []),
      ],
      href: "/accounting?view=debts",
    });
  }
  return result;
}

/** Крупные числа дня. На ТВ — без ссылок: там некому кликать. */
export function KpiTiles({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];
  const items = tiles(m);
  if (items.length === 0) return null;

  return (
    <div
      className={cn("grid min-w-0 gap-3", size === "tv" ? "grid-cols-4 gap-[1vw]" : "sm:grid-cols-2 xl:grid-cols-4")}
    >
      {items.map((tile) => {
        const body = (
          <>
            <div className={cn(s.label, "text-[var(--muted-foreground)]")}>{tile.label}</div>
            <div className="mt-1 flex min-w-0 items-baseline gap-1.5">
              <span title={tile.exact} className={cn(s.value, "truncate font-semibold tracking-tight tabular-nums")}>
                {tile.value}
              </span>
              <span className={cn(s.unit, "shrink-0 text-[var(--muted-foreground)]")}>{tile.unit}</span>
              {tile.tons && (
                <span className={cn(s.unit, "shrink-0 truncate text-[var(--muted-foreground)]")}>· {tile.tons}</span>
              )}
            </div>
            {tile.notes.map((note) => (
              <div key={note} className={cn(s.note, "mt-1 truncate text-[var(--muted-foreground)]")}>
                {note}
              </div>
            ))}
          </>
        );
        const className = cn("min-w-0 rounded-[18px] border bg-[var(--card)] shadow-card", s.pad);
        return size === "tv" ? (
          <div key={tile.key} className={className}>
            {body}
          </div>
        ) : (
          <Link key={tile.key} href={tile.href} className={cn(className, "transition hover:border-[var(--input)]")}>
            {body}
          </Link>
        );
      })}
    </div>
  );
}
