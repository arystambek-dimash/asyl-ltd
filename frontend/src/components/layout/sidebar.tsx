"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Boxes, ClipboardList, Users, Truck,
  ScrollText, BarChart3,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Me } from "@/lib/types";

interface NavItem {
  href: string; label: string; icon: React.ElementType; roles?: string[];
}

const STAFF_NAV: NavItem[] = [
  { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
  { href: "/warehouse", label: "Склад", icon: Boxes },
  { href: "/orders", label: "Заказы", icon: ClipboardList },
  { href: "/clients", label: "Клиенты", icon: Users, roles: ["manager", "boss"] },
  { href: "/shipping", label: "Пост отгрузки", icon: Truck, roles: ["operator", "boss"] },
  { href: "/events", label: "Журнал", icon: ScrollText },
  { href: "/reports", label: "Отчёты", icon: BarChart3 },
];

const PORTAL_NAV: NavItem[] = [
  { href: "/portal/catalog", label: "Каталог", icon: Boxes },
  { href: "/portal/orders/new", label: "Новый заказ", icon: ClipboardList },
  { href: "/portal/orders", label: "Мои заказы", icon: ScrollText },
];

export function Sidebar({ me }: { me: Me }) {
  const pathname = usePathname();
  const nav = me.is_client
    ? PORTAL_NAV
    : STAFF_NAV.filter(
        (i) => !i.roles || me.is_superuser || i.roles.some((r) => me.roles.includes(r))
      );

  return (
    <aside className="flex w-[220px] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)]">
      <div className="flex h-16 items-center gap-2.5 border-b px-5">
        <Image
          src="/logo-mark.png"
          alt="ASYL-LTD"
          width={32}
          height={32}
          className="size-8 shrink-0 object-contain"
          priority
        />
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-tight">ASYL-LTD</div>
          <div className="text-[10px] uppercase tracking-widest text-[var(--muted-foreground)]">
            {me.is_client ? "Кабинет" : "Мельничный комплекс"}
          </div>
        </div>
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 p-3">
        {nav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(item.href + "/");
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-[var(--sidebar-accent)] font-semibold text-[var(--sidebar-accent-foreground)]"
                  : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]"
              )}
            >
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-[var(--primary)]" />
              )}
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
