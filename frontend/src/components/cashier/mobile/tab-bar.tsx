"use client";
import { History, House, QrCode, Send, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CashTabKey } from "../view";

const TABS: Record<CashTabKey, { label: string; icon: React.ElementType }> = {
  home: { label: "Главная", icon: House },
  debts: { label: "Долги", icon: Users },
  pos: { label: "QR", icon: QrCode },
  remote: { label: "Удаленно", icon: Send },
  transactions: { label: "История", icon: History },
};

/** Нижняя панель кассы на телефоне — как в Kaspi: иконка с подписью, активный пункт на цветной плашке. */
export function CashierTabBar({
  tabs,
  active,
  disabled = false,
  onSelect,
}: {
  tabs: CashTabKey[];
  active: CashTabKey | null;
  /** Пока создаётся QR или счёт, переходы ждут ответа — иначе он ляжет не на тот экран. */
  disabled?: boolean;
  onSelect: (tab: CashTabKey) => void;
}) {
  return (
    <nav
      aria-label="Панель кассы"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--border)] bg-[var(--card)] pb-[env(safe-area-inset-bottom)]"
    >
      <ul className="mx-auto grid max-w-md" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}>
        {tabs.map((key) => {
          const tab = TABS[key];
          const current = key === active;
          return (
            <li key={key}>
              <button
                type="button"
                aria-current={current ? "page" : undefined}
                disabled={disabled}
                onClick={() => onSelect(key)}
                className={cn(
                  "flex w-full flex-col items-center gap-0.5 pb-1.5 pt-1.5 text-[11px] font-medium transition-colors disabled:opacity-60",
                  current ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]",
                )}
              >
                <span
                  className={cn(
                    "flex h-8 w-14 items-center justify-center rounded-xl transition-colors",
                    current && "bg-[var(--primary)]/10",
                  )}
                >
                  <tab.icon className="size-6" aria-hidden />
                </span>
                {tab.label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
