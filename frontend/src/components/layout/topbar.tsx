"use client";
import { useEffect, useState, type ReactNode } from "react";
import { LogOut, Sun, Moon, Monitor, Menu, CircleHelp } from "lucide-react";
import { NotificationBell } from "@/components/notification-bell";
import { TOUR_START_EVENT } from "@/components/onboarding-tour";
import { useAuth } from "@/store/auth";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import type { Me } from "@/lib/types";

type Theme = "light" | "dark" | "system";

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.classList.toggle("dark", dark);
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const stored = localStorage.getItem("asyl_theme");
    const saved: Theme = stored === "dark" || stored === "system" ? stored : "light";
    setTheme(saved);
    applyTheme(saved);
  }, []);
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const syncSystemTheme = () => applyTheme("system");
    media.addEventListener("change", syncSystemTheme);
    return () => media.removeEventListener("change", syncSystemTheme);
  }, [theme]);
  function pick(t: Theme) {
    setTheme(t);
    localStorage.setItem("asyl_theme", t);
    applyTheme(t);
  }
  const opts: { key: Theme; icon: React.ElementType; label: string }[] = [
    { key: "light", icon: Sun, label: "Светлая тема" },
    { key: "dark", icon: Moon, label: "Тёмная тема" },
    { key: "system", icon: Monitor, label: "Системная тема" },
  ];
  const currentIndex = opts.findIndex((option) => option.key === theme);
  const current = opts[Math.max(currentIndex, 0)];
  const CurrentIcon = current.icon;
  const next = opts[(Math.max(currentIndex, 0) + 1) % opts.length];

  return (
    <>
      <button
        type="button"
        onClick={() => pick(next.key)}
        className="flex size-11 shrink-0 items-center justify-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] shadow-sm transition hover:bg-[var(--secondary)] hover:text-[var(--foreground)] sm:hidden"
        aria-label={`${current.label}. Переключить на: ${next.label}`}
        title={current.label}
      >
        <CurrentIcon className="size-[18px]" />
      </button>
      <div
        className="hidden items-center gap-0.5 rounded-xl border bg-[var(--muted)]/45 p-1 sm:flex"
        role="group"
        aria-label="Тема оформления"
      >
        {opts.map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => pick(key)}
            aria-label={label}
            aria-pressed={theme === key}
            title={label}
            className={cn(
              "flex size-9 items-center justify-center rounded-lg transition-all",
              theme === key
                ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                : "text-[var(--muted-foreground)] hover:bg-[var(--card)]/55 hover:text-[var(--foreground)]",
            )}
          >
            <Icon className="size-4" />
          </button>
        ))}
      </div>
    </>
  );
}

export function Topbar({
  me,
  title,
  section,
  tabs,
  actions,
  onMenu,
}: {
  me: Me;
  title: string;
  section?: string;
  tabs?: ReactNode;
  actions?: ReactNode;
  onMenu?: () => void;
}) {
  const { logout } = useAuth();
  const router = useRouter();
  const roleText = me.is_client
    ? "Клиент"
    : me.is_monoblock
      ? `Моноблок · ${me.monoblock_camera ?? "без камеры"}`
      : me.is_superuser
        ? "Администратор"
        : me.role_name || "Сотрудник";

  return (
    <header className="relative z-30 flex min-h-[68px] shrink-0 items-center justify-between gap-2 border-b bg-[var(--card)]/92 px-3 pt-[env(safe-area-inset-top)] backdrop-blur-xl sm:h-[72px] sm:px-6 sm:pt-0 lg:px-8">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onMenu}
          className="-ml-1 flex size-11 shrink-0 items-center justify-center rounded-xl text-[var(--muted-foreground)] hover:bg-[var(--secondary)] lg:hidden"
          aria-label="Меню"
        >
          <Menu className="size-5" />
        </button>
        <div className="min-w-0 leading-tight">
          {section && (
            <div className="text-[9px] font-bold uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
              {section}
            </div>
          )}
          <h1 className="truncate text-[17px] font-extrabold tracking-[-0.02em] sm:text-xl">{title}</h1>
        </div>
        {/* Вкладки страницы — в самом навбаре; подчёркивание ложится на его
            нижнюю границу. На телефоне переезжают отдельной строкой ниже. */}
        {tabs && (
          <div
            className="no-scrollbar ml-5 hidden h-[72px] min-w-0 self-stretch overflow-x-auto sm:flex
            [&>div]:h-full [&>div]:border-b-0 [&_button]:h-full [&_button]:whitespace-nowrap"
          >
            {tabs}
          </div>
        )}
      </div>
      <div className="flex min-w-0 shrink-0 items-center gap-2 sm:gap-3">
        <div className="hidden min-w-0 items-center gap-2 sm:flex">{actions}</div>
        {!me.is_client && !me.is_monoblock && (
          <button
            onClick={() => window.dispatchEvent(new Event(TOUR_START_EVENT))}
            className="hidden size-10 items-center justify-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] transition hover:-translate-y-0.5 hover:border-[var(--input)] hover:text-[var(--foreground)] sm:flex"
            title="Обучение по системе"
            aria-label="Обучение по системе"
          >
            <CircleHelp className="size-4" />
          </button>
        )}
        <ThemeToggle />
        {me.is_client && <NotificationBell />}
        <div
          data-tour="profile"
          className="flex min-w-0 items-center gap-1 rounded-xl border bg-[var(--card)] p-1 shadow-[0_8px_24px_-18px_rgba(23,32,27,.55)] sm:gap-2.5 sm:p-1.5 sm:pr-2"
        >
          <div className="hidden size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--primary)] text-[11px] font-bold text-[var(--primary-foreground)] min-[380px]:flex">
            {me.username.slice(0, 2).toUpperCase()}
          </div>
          <div className="hidden min-w-0 leading-tight lg:block">
            <div className="max-w-[150px] truncate text-xs font-semibold">{me.username}</div>
            <div className="mt-0.5 text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">{roleText}</div>
          </div>
          <button
            onClick={() => {
              logout();
              router.push("/login");
            }}
            className="flex size-11 shrink-0 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition hover:bg-[var(--soft-red)] hover:text-[var(--destructive)] sm:size-10"
            title="Выйти"
            aria-label="Выйти"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
