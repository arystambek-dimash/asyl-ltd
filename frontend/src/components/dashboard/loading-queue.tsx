import Link from "next/link";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import { orderStatusLabel } from "@/lib/constants";
import { clientLabel, orderedBagCount } from "@/lib/orders";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCount } from "@/lib/utils";
import { orderTransportText } from "@/lib/wagons";

const VISIBLE_ROWS: Record<BoardSize, number> = { page: 8, tv: 6 };

/** Машины и вагоны на территории: приехали или грузятся. */
export function LoadingQueue({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];
  const shown = m.queue.slice(0, VISIBLE_ROWS[size]);
  const hidden = m.queue.length - shown.length;

  return (
    <section className={cn("flex min-h-0 min-w-0 flex-col rounded-[18px] border bg-[var(--card)] shadow-card", s.pad)}>
      <div className="flex items-baseline justify-between gap-3 border-b pb-2">
        <h3 className={s.title}>На погрузке</h3>
        <span className={cn(s.title, "tabular-nums text-[var(--muted-foreground)]")}>{m.queue.length}</span>
      </div>
      {shown.length === 0 ? (
        <p className={cn(s.row, "py-6 text-[var(--muted-foreground)]")}>Сейчас никто не грузится</p>
      ) : (
        <ul className="min-h-0 flex-1 divide-y overflow-hidden">
          {shown.map((order) => {
            // Две строки: на ТВ колонка узкая, номер с прицепом и клиент в одну не влезают.
            const row = (
              <>
                <span className="flex min-w-0 items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate font-semibold tabular-nums">
                    {orderTransportText(order) || `Заказ #${order.id}`}
                  </span>
                  <span className="shrink-0 font-semibold tabular-nums">{formatCount(orderedBagCount(order))}</span>
                </span>
                <span className={cn(s.note, "block truncate text-[var(--muted-foreground)]")}>
                  {clientLabel(order)} · {orderStatusLabel(order.status)}
                </span>
              </>
            );
            const className = cn(s.row, s.rowGap, "block min-w-0");
            return (
              <li key={order.id}>
                {size === "tv" ? (
                  <div className={className}>{row}</div>
                ) : (
                  <Link href={`/orders/${order.id}`} className={cn(className, "hover:text-[var(--ring)]")}>
                    {row}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {hidden > 0 && <p className={cn(s.note, "mt-auto pt-2 text-[var(--muted-foreground)]")}>ещё {hidden}</p>}
    </section>
  );
}
