import Link from "next/link";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import type { DashboardAttentionKey, DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCompactCurrency } from "@/lib/utils";

type AttentionView = {
  href: string;
  label: string;
  hint: (m: DashboardMetrics) => string;
  urgent?: boolean;
};

const ATTENTION_VIEWS: Record<DashboardAttentionKey, AttentionView> = {
  overdue: {
    href: "/accounting?view=debts",
    label: "Просрочена оплата",
    hint: (m) => formatCompactCurrency(m.overdueTotal, m.overdueCurrency),
    urgent: true,
  },
  payments: {
    href: "/accounting?view=confirm",
    label: "Подтвердить оплаты",
    hint: () => "Ждут кассу",
  },
  orders: {
    href: "/orders",
    label: "Рассмотреть заявки",
    hint: () => "Клиенты ждут ответа",
  },
  stock: {
    href: "/warehouse",
    label: "Минус на складе",
    hint: () => "Исправить остатки",
    urgent: true,
  },
};

export function AttentionPanel({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];

  return (
    <section className={cn("flex min-h-0 min-w-0 flex-col rounded-[18px] border bg-[var(--card)] shadow-card", s.pad)}>
      <div className="flex items-baseline justify-between gap-3 border-b pb-2">
        <h3 className={s.title}>Нужно решить</h3>
        <span
          className={cn(
            s.title,
            "tabular-nums",
            m.attentionCount > 0 ? "text-[var(--warning)]" : "text-[var(--muted-foreground)]",
          )}
        >
          {m.attentionCount}
        </span>
      </div>
      {m.attention.length === 0 ? (
        <p className={cn(s.row, "py-6 text-[var(--success)]")}>Нечего решать</p>
      ) : (
        <ul className="min-h-0 flex-1 divide-y overflow-hidden">
          {m.attention.map(({ key, count }) => {
            const item = ATTENTION_VIEWS[key];
            const row = (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{item.label}</span>
                  <span className={cn(s.note, "block truncate text-[var(--muted-foreground)]")}>{item.hint(m)}</span>
                </span>
                <span
                  className={cn(
                    "shrink-0 font-semibold tabular-nums",
                    item.urgent ? "text-[var(--destructive)]" : "text-[var(--warning)]",
                  )}
                >
                  {count}
                </span>
              </>
            );
            const className = cn(s.row, s.rowGap, "flex min-w-0 items-center gap-3");
            return (
              <li key={key}>
                {size === "tv" ? (
                  <div className={className}>{row}</div>
                ) : (
                  <Link href={item.href} className={cn(className, "hover:text-[var(--ring)]")}>
                    {row}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
