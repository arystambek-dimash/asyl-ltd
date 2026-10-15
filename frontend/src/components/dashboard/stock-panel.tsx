import Link from "next/link";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCount, formatTons, pluralRu } from "@/lib/utils";

/** Сколько строк влезает в панель: на ТВ шрифт крупнее, строк меньше. */
const VISIBLE_ROWS: Record<BoardSize, number> = { page: 12, tv: 10 };

/** Остаток по товарам одного склада: мешки и тонны. Минус — ошибка учёта, красным. */
export function StockPanel({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];
  const shown = m.stockRows.slice(0, VISIBLE_ROWS[size]);
  const rest = m.stockRows.slice(VISIBLE_ROWS[size]);
  const restBags = rest.reduce((sum, row) => sum + row.bags, 0);
  const restText = `ещё ${rest.length} ${pluralRu(rest.length, ["позиция", "позиции", "позиций"])} · ${formatCount(restBags)} меш.`;

  return (
    <section className={cn("flex min-h-0 min-w-0 flex-col rounded-[18px] border bg-[var(--card)] shadow-card", s.pad)}>
      <div className="flex items-baseline justify-between gap-3 border-b pb-2">
        <h3 className={cn(s.title, "min-w-0 truncate")}>Остаток · {m.stockWarehouse}</h3>
        <span className={cn(s.note, "shrink-0 text-[var(--muted-foreground)]")}>мешков · тонн</span>
      </div>
      {shown.length === 0 ? (
        <p className={cn(s.row, "py-6 text-[var(--muted-foreground)]")}>
          {m.settings?.products.length ? "Выбранных товаров на складе нет" : "Склад пуст"}
        </p>
      ) : (
        // Общая сетка строк: колонки мешков и тонн шириной по самому длинному числу —
        // числа стоят ровно друг под другом, а названию остаётся всё остальное.
        <ul className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_auto_auto] content-start divide-y overflow-hidden">
          {shown.map((row) => (
            <li
              key={row.product}
              className={cn(s.row, s.rowGap, "col-span-3 grid grid-cols-subgrid items-baseline gap-x-4", {
                "text-[var(--destructive)]": row.bags < 0,
              })}
            >
              {/* ТВ держит строку в одну линию (высота экрана фиксирована), телефон переносит название. */}
              <span className={cn("min-w-0", size === "tv" ? "truncate" : "break-words")}>{row.title}</span>
              <span className="text-right font-semibold tabular-nums">{formatCount(row.bags)}</span>
              <span
                className={cn(s.rowTons, "text-right tabular-nums", {
                  "text-[var(--muted-foreground)]": row.bags >= 0,
                })}
              >
                {formatTons(row.kg)} т
              </span>
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
