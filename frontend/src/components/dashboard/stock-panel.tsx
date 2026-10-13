import Link from "next/link";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCount, pluralRu } from "@/lib/utils";

/** Сколько строк влезает в панель: на ТВ шрифт крупнее, строк меньше. */
const VISIBLE_ROWS: Record<BoardSize, number> = { page: 12, tv: 10 };

/** Остаток по товарам, сложенный по складам. Минус — ошибка учёта, красным. */
export function StockPanel({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];
  const shown = m.stockRows.slice(0, VISIBLE_ROWS[size]);
  const rest = m.stockRows.slice(VISIBLE_ROWS[size]);
  const restBags = rest.reduce((sum, row) => sum + row.bags, 0);
  const restText = `ещё ${rest.length} ${pluralRu(rest.length, ["позиция", "позиции", "позиций"])} · ${formatCount(restBags)} меш.`;

  return (
    <section className={cn("flex min-h-0 min-w-0 flex-col rounded-[18px] border bg-[var(--card)] shadow-card", s.pad)}>
      <div className="flex items-baseline justify-between gap-3 border-b pb-2">
        <h3 className={s.title}>Остаток по товарам</h3>
        <span className={cn(s.note, "text-[var(--muted-foreground)]")}>мешков</span>
      </div>
      {shown.length === 0 ? (
        <p className={cn(s.row, "py-6 text-[var(--muted-foreground)]")}>Склад пуст</p>
      ) : (
        <ul className="min-h-0 flex-1 divide-y overflow-hidden">
          {shown.map((row) => (
            <li
              key={row.product}
              className={cn(s.row, s.rowGap, "flex min-w-0 items-baseline justify-between gap-4", {
                "text-[var(--destructive)]": row.bags < 0,
              })}
            >
              <span className="min-w-0 truncate">{row.title}</span>
              <span className="shrink-0 font-semibold tabular-nums">{formatCount(row.bags)}</span>
            </li>
          ))}
        </ul>
      )}
      {rest.length > 0 &&
        (size === "tv" ? (
          <p className={cn(s.note, "mt-auto pt-2 text-[var(--muted-foreground)]")}>{restText}</p>
        ) : (
          <Link href="/warehouse" className={cn(s.note, "mt-auto pt-2 text-[var(--ring)] hover:underline")}>
            {restText}
          </Link>
        ))}
    </section>
  );
}
