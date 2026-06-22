"use client";
import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Boxes, ClipboardList, Users, Truck,
  ScrollText, BarChart3, Package, ChevronDown, ChevronRight, Settings, Video,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { can } from "@/lib/can";
import type { Me } from "@/lib/types";

interface NavChild { href: string; label: string; }
interface NavItem {
  href?: string;
  label: string;
  icon: React.ElementType;
  perm?: string;
  children?: NavChild[];
}
interface NavSection { title: string; items: NavItem[]; }

const STAFF_SECTIONS: NavSection[] = [
  {
    title: "Обзор",
    items: [
      { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
      { href: "/reports", label: "Отчёты", icon: BarChart3, perm: "reports.view" },
    ],
  },
  {
    title: "Работа",
    items: [
      { href: "/orders", label: "Заказы", icon: ClipboardList, perm: "orders.view" },
      { href: "/shipping", label: "Пост отгрузки", icon: Truck, perm: "shipping.view" },
      { href: "/warehouse", label: "Склад", icon: Boxes, perm: "warehouse.view" },
      { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
      {
        label: "Номенклатура", icon: Package, perm: "catalog.view",
        children: [
          { href: "/catalog/grades", label: "Сорта" },
          { href: "/catalog/packagings", label: "Фасовки" },
          { href: "/catalog/products", label: "Товары" },
        ],
      },
    ],
  },
  {
    title: "Управление",
    items: [
      { href: "/events", label: "Журнал", icon: ScrollText, perm: "events.view" },
      { href: "/management/cameras", label: "Камеры", icon: Video, perm: "cameras.view" },
      {
        label: "Доступы", icon: Settings, perm: "employees.view",
        children: [
          { href: "/management/employees", label: "Сотрудники" },
          { href: "/management/roles", label: "Роли" },
        ],
      },
    ],
  },
];

const PORTAL_SECTIONS: NavSection[] = [
  {
    title: "Кабинет",
    items: [
      { href: "/portal/catalog", label: "Каталог", icon: Boxes },
      { href: "/portal/orders/new", label: "Новый заказ", icon: ClipboardList },
      { href: "/portal/orders", label: "Мои заказы", icon: ScrollText },
    ],
  },
];

function NavLeaf({ href, label, icon: Icon }: { href: string; label: string; icon: React.ElementType }) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(href + "/");
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
        active
          ? "bg-[var(--sidebar-accent)] font-medium text-[var(--sidebar-accent-foreground)]"
          : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]"
      )}
    >
      <Icon className="size-[18px] shrink-0" />
      {label}
    </Link>
  );
}

function NavGroup({ item }: { item: NavItem }) {
  const pathname = usePathname();
  const Icon = item.icon;
  const childActive = item.children!.some(
    (c) => pathname === c.href || pathname.startsWith(c.href + "/")
  );
  const [open, setOpen] = useState(childActive);

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
          childActive
            ? "font-medium text-[var(--sidebar-foreground)]"
            : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]"
        )}
      >
        <Icon className="size-[18px] shrink-0" />
        <span className="flex-1 text-left">{item.label}</span>
        {open ? <ChevronDown className="size-3.5 opacity-60" /> : <ChevronRight className="size-3.5 opacity-60" />}
      </button>
      {open && (
        <div className="mt-0.5 flex flex-col gap-0.5 pl-[26px]">
          {item.children!.map((c) => {
            const active = pathname === c.href || pathname.startsWith(c.href + "/");
            return (
              <Link
                key={c.href}
                href={c.href}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                  active
                    ? "bg-[var(--sidebar-accent)] font-medium text-[var(--sidebar-accent-foreground)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]"
                )}
              >
                {c.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function Sidebar({ me }: { me: Me }) {
  const sections = me.is_client ? PORTAL_SECTIONS : STAFF_SECTIONS;
  const visible = sections
    .map((s) => ({ ...s, items: s.items.filter((i) => !i.perm || can(me, i.perm)) }))
    .filter((s) => s.items.length > 0);

  const initials = me.username.slice(0, 2).toUpperCase();

  return (
    <aside className="flex w-[248px] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)]">
      {/* профиль вверху */}
      <div className="flex items-center gap-2.5 px-3 py-3">
        <Image src="/logo-mark.png" alt="ASYL-LTD" width={28} height={28}
          className="size-7 shrink-0 rounded-md object-contain" priority />
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[13px] font-semibold">ASYL-LTD</div>
          <div className="truncate text-[11px] text-[var(--muted-foreground)]">
            {me.is_client ? "Кабинет клиента" : "Мельничный комплекс"}
          </div>
        </div>
      </div>

      {/* навигация по группам */}
      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-3">
        {visible.map((section) => (
          <div key={section.title} className="flex flex-col gap-0.5">
            <div className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--muted-foreground)]/70">
              {section.title}
            </div>
            {section.items.map((item) =>
              item.children
                ? <NavGroup key={item.label} item={item} />
                : <NavLeaf key={item.href} href={item.href!} label={item.label} icon={item.icon} />
            )}
          </div>
        ))}
      </nav>

      {/* футер */}
      <div className="flex items-center justify-between border-t px-4 py-2.5 text-[11px] text-[var(--muted-foreground)]">
        <span className="flex items-center gap-1.5">
          <span className="size-1.5 rounded-full bg-[var(--success)]" /> {initials} · В сети
        </span>
        <span>v1.0</span>
      </div>
    </aside>
  );
}
