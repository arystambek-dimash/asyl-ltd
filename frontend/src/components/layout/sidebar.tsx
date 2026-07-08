"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Boxes, ClipboardList, Users, Truck,
  ScrollText, BarChart3, Package, ChevronDown, ChevronRight, Settings, X, Store, Wallet, TrainFront,
  Briefcase, Calculator, HandCoins, MapPin,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { can, deptLabel, isDept2Only } from "@/lib/can";
import type { Me } from "@/lib/types";

interface NavChild { href: string; label: string; perm?: string; superuser?: boolean; }
interface NavItem {
  href?: string;
  label: string;
  icon: React.ElementType;
  perm?: string;
  children?: NavChild[];
}
interface NavSection { title: string; items: NavItem[]; }

// Название второго отдела редактируется админом — секции строятся динамически.
function staffSections(fieldName: string): NavSection[] {
  return [
    {
      title: "Обзор",
      items: [
        { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
        { href: "/debts", label: "Долги", icon: Wallet, perm: "reports.view" },
        { href: "/reports", label: "Отчёты", icon: BarChart3, perm: "reports.view" },
      ],
    },
    {
      title: "Работа",
      items: [
        { href: "/orders", label: "Заказы", icon: ClipboardList, perm: "orders.view" },
        { href: "/accounting", label: "Табло бухгалтера", icon: Calculator, perm: "payments.confirm" },
        { href: "/cashier", label: "Касса", icon: HandCoins, perm: "payments.cashier" },
        { href: "/shipping", label: "Пост отгрузки", icon: Truck, perm: "shipping.view" },
        { href: "/train", label: "Поезда", icon: TrainFront, perm: "train.view" },
        { href: "/warehouse", label: "Склад", icon: Boxes, perm: "warehouse.view" },
        { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
        { href: "/stores", label: "Магазины", icon: Store, perm: "clients.view" },
        { href: "/catalog/products", label: "Товары", icon: Package, perm: "catalog.view" },
      ],
    },
    {
      title: `Отдел «${fieldName}»`,
      items: [
        { href: "/city/orders", label: `Заявки ${fieldName}`, icon: Briefcase, perm: "dept2.view" },
        { href: "/city/clients", label: `Клиенты ${fieldName}`, icon: MapPin, perm: "dept2.view" },
      ],
    },
    {
      title: "Управление",
      items: [
        { href: "/events", label: "Журнал", icon: ScrollText, perm: "events.view" },
        {
          label: "Доступы", icon: Settings,
          children: [
            { href: "/management/employees", label: "Сотрудники", perm: "employees.view" },
            { href: "/management/roles", label: "Роли", perm: "rbac.view" },
            // Переименование отделов — только суперадмину.
            { href: "/management/departments", label: "Отделы", superuser: true },
          ],
        },
      ],
    },
  ];
}

const PORTAL_SECTIONS: NavSection[] = [
  {
    title: "Кабинет",
    items: [
      { href: "/portal/catalog", label: "Товары", icon: Boxes },
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
      data-tour={`nav:${href}`}
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

function SidebarContent({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  const sections = me.is_client ? PORTAL_SECTIONS : staffSections(deptLabel(me, "field"));
  const visible = sections
    .map((s) => ({
      ...s,
      items: s.items
        .map((i) => i.children
          ? { ...i, children: i.children.filter((c) =>
              (!c.perm || can(me, c.perm)) && (!c.superuser || me.is_superuser)) }
          : i)
        .filter((i) => (!i.perm || can(me, i.perm)) && (!i.children || i.children.length > 0))
        // Менеджеру выездного отдела дашборд комплекса не показываем.
        .filter((i) => !(i.href === "/dashboard" && isDept2Only(me))),
    }))
    .filter((s) => s.items.length > 0);

  const initials = me.username.slice(0, 2).toUpperCase();

  return (
    <>
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
      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-3" onClick={onNavigate}>
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
    </>
  );
}

export function Sidebar({
  me, mobileOpen = false, onClose,
}: { me: Me; mobileOpen?: boolean; onClose?: () => void }) {
  const pathname = usePathname();

  // Закрываем мобильную панель при смене маршрута.
  useEffect(() => { onClose?.(); }, [pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {/* десктоп: постоянный сайдбар */}
      <aside data-tour="nav"
        className="hidden w-[248px] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)] md:flex">
        <SidebarContent me={me} />
      </aside>

      {/* мобайл: выезжающая панель с оверлеем */}
      <div
        className={cn("fixed inset-0 z-50 md:hidden", mobileOpen ? "" : "pointer-events-none")}
        aria-hidden={!mobileOpen}
      >
        <div
          className={cn("absolute inset-0 bg-black/50 transition-opacity",
            mobileOpen ? "opacity-100" : "opacity-0")}
          onClick={onClose}
        />
        <aside
          className={cn(
            "absolute inset-y-0 left-0 flex w-[248px] max-w-[80vw] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)] shadow-xl transition-transform",
            mobileOpen ? "translate-x-0" : "-translate-x-full"
          )}
        >
          <button
            onClick={onClose}
            className="absolute right-2 top-2 flex size-8 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60"
            aria-label="Закрыть меню"
          >
            <X className="size-4" />
          </button>
          <SidebarContent me={me} onNavigate={onClose} />
        </aside>
      </div>
    </>
  );
}
