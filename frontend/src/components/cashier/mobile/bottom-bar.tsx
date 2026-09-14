"use client";
import { cn } from "@/lib/utils";

export interface BottomBarItem<K extends string> {
  key: K;
  label: string;
  icon: React.ElementType;
  /** Всегда на цветной плашке — главная кнопка панели (POS), даже когда экран другой. */
  accent?: boolean;
}

/**
 * Нижняя навигационная панель, как на телефоне: иконка с подписью, активный пункт на цветной плашке.
 * Рендерится слотом `footer` у AppShell — под прокруткой контента, всегда у нижнего края экрана.
 */
export function BottomBar<K extends string>({
  label,
  items,
  active,
  disabled = false,
  onSelect,
}: {
  label: string;
  items: BottomBarItem<K>[];
  active: K | null;
  /** Пока создаётся QR или счёт, переходы ждут ответа — иначе он ляжет не на тот экран. */
  disabled?: boolean;
  onSelect: (key: K) => void;
}) {
  return (
    <nav
      aria-label={label}
      className="shrink-0 border-t border-[var(--border)] bg-[var(--card)] pb-[env(safe-area-inset-bottom)]"
    >
      <ul className="mx-auto grid max-w-md" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
        {items.map((item) => {
          const current = item.key === active;
          const highlighted = current || Boolean(item.accent);
          return (
            <li key={item.key}>
              <button
                type="button"
                aria-current={current ? "page" : undefined}
                disabled={disabled}
                onClick={() => onSelect(item.key)}
                className={cn(
                  "flex w-full flex-col items-center gap-0.5 pb-1.5 pt-1.5 text-[11px] font-medium transition-colors disabled:opacity-60",
                  highlighted ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]",
                )}
              >
                <span
                  className={cn(
                    "flex h-8 w-14 items-center justify-center rounded-xl transition-colors",
                    highlighted && "bg-[var(--primary)]/10",
                  )}
                >
                  <item.icon className="size-6" aria-hidden />
                </span>
                {item.label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
