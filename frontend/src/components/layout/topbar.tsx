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
  const dark = theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.classList.toggle("dark", dark);
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const saved = (localStorage.getItem("asyl_theme") as Theme) || "light";
    setTheme(saved); applyTheme(saved);
  }, []);
  function pick(t: Theme) {
    setTheme(t); localStorage.setItem("asyl_theme", t); applyTheme(t);
  }
  const opts: { key: Theme; icon: React.ElementType }[] = [
    { key: "light", icon: Sun }, { key: "dark", icon: Moon }, { key: "system", icon: Monitor },
  ];
  return (
    <div className="flex items-center gap-0.5 rounded-lg border p-0.5">
      {opts.map(({ key, icon: Icon }) => (
        <button key={key} onClick={() => pick(key)}
          className={cn("flex size-7 items-center justify-center rounded-md transition-colors",
            theme === key ? "bg-[var(--secondary)] text-[var(--foreground)]"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
          <Icon className="size-4" />
        </button>
      ))}
    </div>
  );
}

export function Topbar({ me, title, section, tabs, actions, onMenu }: {
  me: Me; title: string; section?: string;
  tabs?: ReactNode; actions?: ReactNode; onMenu?: () => void;
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
    <header className="flex h-16 items-center justify-between gap-2 border-b px-4 sm:px-8">
      <div className="flex min-w-0 items-center gap-2">
        <button
          onClick={onMenu}
          className="-ml-1 flex size-9 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:bg-[var(--secondary)] md:hidden"
          aria-label="Меню"
        >
          <Menu className="size-5" />
        </button>
        <div className="min-w-0 leading-tight">
          {section && (
            <div className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              {section}
            </div>
          )}
          <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">{title}</h1>
        </div>
        {/* Вкладки страницы — в самом навбаре; подчёркивание ложится на его
            нижнюю границу. На телефоне переезжают отдельной строкой ниже. */}
        {tabs && (
          <div className="ml-4 hidden h-16 min-w-0 self-stretch overflow-x-auto sm:flex
            [&>div]:h-full [&>div]:border-b-0 [&_button]:h-full [&_button]:whitespace-nowrap">
            {tabs}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 sm:gap-3">
        {actions}
        {!me.is_client && !me.is_monoblock && (
          <button
            onClick={() => window.dispatchEvent(new Event(TOUR_START_EVENT))}
            className="hidden size-8 items-center justify-center rounded-lg border text-[var(--muted-foreground)] hover:text-[var(--foreground)] sm:flex"
            title="Обучение по системе"
            aria-label="Обучение по системе"
          >
            <CircleHelp className="size-4" />
          </button>
        )}
        <ThemeToggle />
        {me.is_client && <NotificationBell />}
        <div data-tour="profile" className="flex items-center gap-2.5 border-l pl-3">
          <div className="flex size-8 items-center justify-center rounded-full bg-[var(--secondary)] text-xs font-semibold">
            {me.username.slice(0, 2).toUpperCase()}
          </div>
          <div className="hidden leading-tight sm:block">
            <div className="max-w-[180px] truncate text-sm font-medium">{me.username}</div>
            <div className="text-[10px] text-[var(--muted-foreground)]">{roleText}</div>
          </div>
          <button
            onClick={() => { logout(); router.push("/login"); }}
            className="ml-1 text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
            title="Выйти"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
