"use client";
import { ChevronRight } from "lucide-react";

interface NavListItem {
  key: string;
  icon: React.ElementType;
  title: string;
  subtitle?: string;
  onSelect: () => void;
}

/** Список-меню как в мобильных банках: иконка, название, подзаголовок, «›». */
export function NavList({ items, label }: { items: NavListItem[]; label: string }) {
  return (
    <nav
      aria-label={label}
      className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card"
    >
      <ul className="divide-y divide-[var(--border)]">
        {items.map((item) => (
          <li key={item.key}>
            <button
              type="button"
              onClick={item.onSelect}
              className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60 focus-visible:bg-[var(--muted)]/60 focus-visible:outline-none"
            >
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
              <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
