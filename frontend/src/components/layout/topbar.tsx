"use client";
import { Bell, LogOut } from "lucide-react";
import { useAuth } from "@/store/auth";
import { useRouter } from "next/navigation";
import type { Me } from "@/lib/types";

export function Topbar({ me, title }: { me: Me; title: string }) {
  const { logout } = useAuth();
  const router = useRouter();
  const roleText = me.is_client
    ? "Клиент"
    : me.is_superuser
    ? "Администратор"
    : me.role_name || "Сотрудник";

  return (
    <header className="flex h-16 items-center justify-between border-b px-8">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        <p className="text-xs text-[var(--muted-foreground)]">
          {new Date().toLocaleDateString("ru-RU", {
            day: "numeric", month: "long", year: "numeric",
          })}
        </p>
      </div>
      <div className="flex items-center gap-4">
        <button className="relative text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
          <Bell className="size-5" />
          <span className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-[var(--destructive)]" />
        </button>
        <div className="flex items-center gap-2.5 border-l pl-4">
          <div className="flex size-8 items-center justify-center rounded-full bg-[var(--secondary)] text-xs font-semibold">
            {me.username.slice(0, 2).toUpperCase()}
          </div>
          <div className="leading-tight">
            <div className="text-sm font-medium">{me.username}</div>
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
