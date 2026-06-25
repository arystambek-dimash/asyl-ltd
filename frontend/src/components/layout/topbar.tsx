"use client";
import { useEffect, useState, type ReactNode } from "react";
import { LogOut, Sun, Moon, Monitor, Menu } from "lucide-react";
import { NotificationBell } from "@/components/notification-bell";
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

export function Topbar({ me, title, section, actions, onMenu }: { me: Me; title: string; section?: string; actions?: ReactNode; onMenu?: () => void }) {
  const { logout } = useAuth();
  const router = useRouter();
  const roleText = me.is_client
    ? "Клиент"
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
      </div>
      <div className="flex items-center gap-2 sm:gap-3">
        {actions}
        <ThemeToggle />
        {me.is_client && <NotificationBell />}
        <div className="flex items-center gap-2.5 border-l pl-3">
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
