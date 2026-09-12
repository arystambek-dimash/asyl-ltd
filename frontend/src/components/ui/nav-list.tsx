"use client";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface NavListItem {
  key: string;
  icon: React.ElementType;
  title: string;
  subtitle?: string;
  /** Значение справа (сумма, счётчик) — табличными цифрами. */
  value?: string;
  href?: string;
  onSelect?: () => void;
}

const ROW_CLASS =
  "flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60 focus-visible:bg-[var(--muted)]/60 focus-visible:outline-none";

/** Список-меню как в мобильных банках: иконка, название, подзаголовок, «›». */
export function NavList({ items, label, className }: { items: NavListItem[]; label: string; className?: string }) {
  return (
    <nav
      aria-label={label}
      className={cn("overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card", className)}
    >
      <ul className="divide-y divide-[var(--border)]">
        {items.map((item) => {
          const content = (
            <>
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--foreground)]">
                <item.icon className="size-5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold leading-tight">{item.title}</span>
                {item.subtitle && (
                  <span className="mt-0.5 line-clamp-2 block text-[13px] text-[var(--muted-foreground)]">
                    {item.subtitle}
                  </span>
                )}
              </span>
              {item.value && <span className="shrink-0 text-[15px] font-semibold tabular-nums">{item.value}</span>}
              <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
            </>
          );
          return (
            <li key={item.key}>
              {item.href ? (
                <Link href={item.href} className={ROW_CLASS}>
                  {content}
                </Link>
              ) : (
                <button type="button" onClick={item.onSelect} className={ROW_CLASS}>
                  {content}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
