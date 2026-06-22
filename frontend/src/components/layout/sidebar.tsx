"use client";
import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Boxes, ClipboardList, Users, Truck,
  ScrollText, BarChart3, Package, ChevronDown, Settings,
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

const STAFF_NAV: NavItem[] = [
  { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
  {
    label: "Номенклатура", icon: Package, perm: "catalog.view",
    children: [
      { href: "/catalog/grades", label: "Сорта" },
      { href: "/catalog/packagings", label: "Фасовки" },
      { href: "/catalog/products", label: "Товары" },
    ],
  },
  { href: "/warehouse", label: "Склад", icon: Boxes, perm: "warehouse.view" },
  { href: "/orders", label: "Заказы", icon: ClipboardList, perm: "orders.view" },
  { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
  { href: "/shipping", label: "Пост отгрузки", icon: Truck, perm: "shipping.view" },
  { href: "/events", label: "Журнал", icon: ScrollText, perm: "events.view" },
  { href: "/reports", label: "Отчёты", icon: BarChart3, perm: "reports.view" },
  {
    label: "Управление", icon: Settings, perm: "employees.view",
    children: [
      { href: "/management/employees", label: "Сотрудники" },
      { href: "/management/roles", label: "Роли" },
    ],
  },
];

const PORTAL_NAV: NavItem[] = [
  { href: "/portal/catalog", label: "Каталог", icon: Boxes },
  { href: "/portal/orders/new", label: "Новый заказ", icon: ClipboardList },
  { href: "/portal/orders", label: "Мои заказы", icon: ScrollText },
];

function NavLeaf({ href, label, icon: Icon }: { href: string; label: string; icon: React.ElementType }) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(href + "/");
  return (
    <Link
      href={href}
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
          "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
          childActive
            ? "font-semibold text-[var(--sidebar-foreground)]"
            : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]"
        )}
      >
        <Icon className="size-4" />
        <span className="flex-1 text-left">{item.label}</span>
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-0.5 flex flex-col gap-0.5 pl-9">
          {item.children!.map((c) => {
            const active = pathname === c.href || pathname.startsWith(c.href + "/");
            return (
              <Link
                key={c.href}
                href={c.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm transition-colors",
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
  const nav = me.is_client
    ? PORTAL_NAV
    : STAFF_NAV.filter((i) => !i.perm || can(me, i.perm));

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
        {nav.map((item) =>
          item.children ? (
            <NavGroup key={item.label} item={item} />
          ) : (
            <NavLeaf key={item.href} href={item.href!} label={item.label} icon={item.icon} />
          )
        )}
      </nav>
    </aside>
  );
}
