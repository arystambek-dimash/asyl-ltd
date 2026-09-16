"use client";
import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, LogOut, Monitor, Moon, Sun } from "lucide-react";
import type { Me } from "@/lib/types";
import { useDismiss } from "@/lib/use-dismiss";
import { cn } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type Theme = "light" | "dark" | "system";

const THEME_KEY = "asyl_theme";

const THEMES: { key: Theme; icon: React.ElementType; label: string; short: string }[] = [
  { key: "light", icon: Sun, label: "Светлая тема", short: "Светлая" },
  { key: "dark", icon: Moon, label: "Тёмная тема", short: "Тёмная" },
  { key: "system", icon: Monitor, label: "Системная тема", short: "Авто" },
];

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Тема живёт в кнопке профиля, а не в меню: меню монтируется по клику, а тему надо применить при загрузке. */
function useTheme() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(THEME_KEY);
    } catch {
      // Приватный режим без localStorage — остаёмся на светлой.
    }
    const saved: Theme = stored === "dark" || stored === "system" ? stored : "light";
    setTheme(saved);
    applyTheme(saved);
  }, []);
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => applyTheme("system");
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [theme]);
  function pick(next: Theme) {
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Не сохранится между визитами, но на этой странице применится.
    }
    applyTheme(next);
  }
  return { theme, pick };
}

/** Аватар в шапке: по нажатию — кто вошёл, тема оформления и выход. */
export function ProfileMenu({ me, accountLabel }: { me: Me; accountLabel: string }) {
  const { logout } = useAuth();
  const router = useRouter();
  const { theme, pick } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const initials = me.username.slice(0, 2).toUpperCase();

  useDismiss(rootRef, () => setOpen(false), open);

  return (
    <div
      ref={rootRef}
      className="relative border-l pl-3"
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open) return;
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        data-tour="profile"
        onClick={() => setOpen((current) => !current)}
        aria-label={`Профиль: ${me.username}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        className="flex items-center gap-2.5 rounded-lg p-0.5 pr-1 transition-colors hover:bg-[var(--secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
      >
        <span className="flex size-8 items-center justify-center rounded-full bg-[var(--secondary)] text-xs font-semibold">
          {initials}
        </span>
        <span className="hidden text-left leading-tight sm:block">
          <span className="block max-w-[180px] truncate text-sm font-medium">{me.username}</span>
          <span className="block text-[10px] text-[var(--muted-foreground)]">{accountLabel}</span>
        </span>
        <ChevronDown
          className={cn(
            "hidden size-4 text-[var(--muted-foreground)] transition-transform sm:block",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Профиль"
          className="absolute right-0 top-11 z-50 w-64 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-lg"
        >
          <div className="flex items-center gap-3 border-b px-4 py-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[var(--secondary)] text-xs font-semibold">
              {initials}
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-sm font-medium">{me.username}</div>
              <div className="text-xs text-[var(--muted-foreground)]">{accountLabel}</div>
            </div>
          </div>

          <div className="border-b px-4 py-3">
            <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              Тема
            </div>
            <div
              role="group"
              aria-label="Тема оформления"
              className="grid grid-cols-3 gap-1 rounded-lg bg-[var(--muted)] p-1"
            >
              {THEMES.map(({ key, icon: Icon, label, short }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => pick(key)}
                  aria-label={label}
                  aria-pressed={theme === key}
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-md px-1 py-1.5 text-[11px] font-medium transition-colors",
                    theme === key
                      ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                      : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                  )}
                >
                  <Icon className="size-4" />
                  {short}
                </button>
              ))}
            </div>
          </div>

          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
              router.push("/login");
            }}
            className="flex w-full items-center gap-2.5 px-4 py-3 text-left text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--destructive)]/8 hover:text-[var(--destructive)]"
          >
            <LogOut className="size-4" />
            Выйти
          </button>
        </div>
      )}
    </div>
  );
}
