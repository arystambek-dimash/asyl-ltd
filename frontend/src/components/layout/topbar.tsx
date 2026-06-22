"use client";
import { useEffect, useState } from "react";
import { Bell, LogOut, Sun, Moon, Monitor } from "lucide-react";
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

export function Topbar({ me, title, section }: { me: Me; title: string; section?: string }) {
  const { logout } = useAuth();
  const router = useRouter();
  const roleText = me.is_client
    ? "Клиент"
    : me.is_superuser
    ? "Администратор"
    : me.role_name || "Сотрудник";

  return (
    <header className="flex h-16 items-center justify-between border-b px-8">
      <div className="leading-tight">
        {section && (
          <div className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            {section}
          </div>
        )}
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      </div>
      <div className="flex items-center gap-3">
        <ThemeToggle />
        <button className="relative text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
          <Bell className="size-5" />
          <span className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-[var(--destructive)]" />
        </button>
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
