"use client";
import { useEffect, useRef } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import logoMark from "@/app/icon.png";
import {
  LayoutDashboard,
  Boxes,
  ClipboardList,
  ShoppingCart,
  Truck,
  Users,
  ScrollText,
  ListChecks,
  BarChart3,
  Package,
  Settings,
  X,
  Store,
  HandCoins,
  ScanLine,
  Warehouse,
  Wheat,
  MessageCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { canAny, type Perm } from "@/lib/can";
import { CASHIER_ENTRY_PERMS } from "@/components/cashier/view";
import { focusedElement, restoreFocus, trapTab } from "@/lib/focus";
import type { Me } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  /** Нужно ЛЮБОЕ из прав, как в RequirePerm; без perm пункт виден всем. */
  perm?: Perm;
}
interface NavSection {
  title: string;
  items: NavItem[];
}

function navItemVisible(me: Me, item: NavItem): boolean {
  return !item.perm || canAny(me, item.perm);
}

const STAFF_SECTIONS: NavSection[] = [
  {
    title: "Обзор",
    items: [
      { href: "/dashboard", label: "Главная", icon: LayoutDashboard },
      { href: "/reports", label: "Отчёты", icon: BarChart3, perm: "reports.view" },
    ],
  },
  {
    title: "Работа",
    items: [
      { href: "/orders", label: "Заказы", icon: ClipboardList, perm: "orders.view" },
      // Касса: подтверждение оплат + вкладки «Долги» и «Транзакции».
      { href: "/accounting", label: "Касса", icon: HandCoins, perm: CASHIER_ENTRY_PERMS },
      // Моноблок только для просмотра: очередь машин и вагонов, камеры и AI-подсчёт — одно право.
      { href: "/monoblock", label: "Моноблок", icon: ScanLine, perm: "monoblock.view" },
      // Грузчик: очередь к отгрузке, одна кнопка «Отгружено» и печать накладной.
      { href: "/loader", label: "Грузчик", icon: Truck, perm: "loader.view" },
      { href: "/warehouse", label: "Склады", icon: Boxes, perm: "warehouse.view" },
      // Силосы имеют отдельное право независимо от зернового процесса.
      { href: "/warehouse/silos", label: "Силосы", icon: Warehouse, perm: "silos.view" },
      // Проходная вагонов: заявки, приход, взвешивание и выход.
      { href: "/grain", label: "Приход и вывоз", icon: Wheat, perm: "grain.view" },
      { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
      { href: "/stores", label: "Магазины", icon: Store, perm: "stores.view" },
      { href: "/catalog/products", label: "Товары", icon: Package, perm: "catalog.view" },
      // Без perm: свои задачи доступны каждому сотруднику, иначе исполнитель
      // не смог бы открыть то, что ему поручили.
      { href: "/tasks", label: "Задачи", icon: ListChecks },
    ],
  },
  {
    title: "Управление",
    items: [
      { href: "/events", label: "Журнал", icon: ScrollText, perm: "events.view" },
      // Отчёты о вагонах из WhatsApp: что бот провёл сам и что ждёт человека.
      { href: "/management/whatsapp-bot", label: "WhatsApp-бот", icon: MessageCircle, perm: "bots.view" },
      {
        href: "/management/employees",
        label: "Сотрудники",
        icon: Settings,
        perm: "employees.view",
      },
    ],
  },
];

/** Виден ли сотруднику пункт меню — обучение показывает шаги только по видимым разделам. */
export function canSeeStaffNav(me: Me, href: string): boolean {
  const item = STAFF_SECTIONS.flatMap((section) => section.items).find((navItem) => navItem.href === href);
  return !!item && navItemVisible(me, item);
}

const PORTAL_SECTIONS: NavSection[] = [
  {
    title: "Кабинет",
    items: [
      { href: "/portal/catalog", label: "Товары", icon: Boxes },
      { href: "/portal/cart", label: "Корзина", icon: ShoppingCart },
      { href: "/portal/orders", label: "Мои заказы", icon: ScrollText },
    ],
  },
];

// Активен только самый специфичный из совпавших пунктов: без этого на
// /portal/orders/42 горели бы и вложенный пункт, и «Мои заказы» (/portal/orders).
function findActiveHref(sections: NavSection[], pathname: string): string | undefined {
  return sections
    .flatMap((section) => section.items.map((item) => item.href))
    .filter((href) => pathname === href || pathname.startsWith(href + "/"))
    .sort((a, b) => b.length - a.length)[0];
}

function NavLeaf({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: React.ElementType;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      data-tour={`nav:${href}`}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
        active
          ? "bg-[var(--sidebar-accent)] font-medium text-[var(--sidebar-accent-foreground)]"
          : "text-[var(--muted-foreground)] hover:bg-[var(--sidebar-accent)]/60 hover:text-[var(--sidebar-foreground)]",
      )}
    >
      <Icon className="size-[18px] shrink-0" />
      {label}
    </Link>
  );
}

function SidebarContent({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  const pathname = usePathname();
  const sections: NavSection[] = me.is_client ? PORTAL_SECTIONS : STAFF_SECTIONS;
  const visible = sections
    .map((s) => ({
      ...s,
      items: s.items.filter((item) => navItemVisible(me, item)),
    }))
    .filter((s) => s.items.length > 0);

  const activeHref = findActiveHref(visible, pathname);

  return (
    <>
      {/* логотип */}
      <div className="flex items-center gap-2.5 px-3 py-3">
        <Image
          src={logoMark}
          alt="ASYL-LTD"
          width={28}
          height={28}
          className="size-7 shrink-0 rounded-md object-contain"
          priority
        />
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
            {section.items.map((item) => (
              <NavLeaf
                key={item.href}
                href={item.href}
                label={item.label}
                icon={item.icon}
                active={item.href === activeHref}
              />
            ))}
          </div>
        ))}
      </nav>
    </>
  );
}

export function Sidebar({ me, mobileOpen = false, onClose }: { me: Me; mobileOpen?: boolean; onClose?: () => void }) {
  const pathname = usePathname();
  const mobilePanelRef = useRef<HTMLElement>(null);
  const mobileCloseRef = useRef<HTMLButtonElement>(null);

  // Закрываем мобильную панель при смене маршрута.
  useEffect(() => {
    onClose?.();
  }, [onClose, pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const restoreTarget = focusedElement();
    const focusFrame = requestAnimationFrame(() => mobileCloseRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
        return;
      }
      if (mobilePanelRef.current) trapTab(event, mobilePanelRef.current);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
      restoreFocus(restoreTarget);
    };
  }, [mobileOpen, onClose]);

  return (
    <>
      {/* десктоп: постоянный сайдбар */}
      <aside
        data-tour="nav"
        className="hidden w-[248px] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)] lg:flex"
      >
        <SidebarContent me={me} />
      </aside>

      {/* мобайл: выезжающая панель с оверлеем */}
      <div
        className={cn("fixed inset-0 z-50 lg:hidden", mobileOpen ? "" : "pointer-events-none")}
        aria-hidden={!mobileOpen}
        inert={!mobileOpen}
      >
        <div
          className={cn("absolute inset-0 bg-black/50 transition-opacity", mobileOpen ? "opacity-100" : "opacity-0")}
          onClick={onClose}
        />
        <aside
          ref={mobilePanelRef}
          role="dialog"
          aria-modal="true"
          aria-label="Меню навигации"
          tabIndex={-1}
          className={cn(
            "absolute inset-y-0 left-0 flex w-[248px] max-w-[80vw] flex-col border-r bg-[var(--sidebar)] text-[var(--sidebar-foreground)] shadow-xl transition-transform",
            mobileOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <button
            ref={mobileCloseRef}
            type="button"
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
