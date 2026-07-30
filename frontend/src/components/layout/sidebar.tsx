"use client";
import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Boxes,
  ClipboardList,
  Users,
  Truck,
  ScrollText,
  ListChecks,
  BarChart3,
  Package,
  ChevronDown,
  ChevronRight,
  Settings,
  X,
  Store,
  HandCoins,
  ScanLine,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { can } from "@/lib/can";
import type { Me } from "@/lib/types";

// perm — строка или массив (нужно ЛЮБОЕ из прав), как в RequirePerm.
type Perm = string | string[];
interface NavChild {
  href: string;
  label: string;
  perm?: Perm;
  superuser?: boolean;
}
interface NavItem {
  href?: string;
  label: string;
  icon: React.ElementType;
  perm?: Perm;
  children?: NavChild[];
}
interface NavSection {
  title: string;
  items: NavItem[];
}

// Пункт виден, если у пользователя есть ЛЮБОЕ из перечисленных прав.
function hasNavPerm(me: Me, perm?: Perm): boolean {
  if (!perm) return true;
  return (Array.isArray(perm) ? perm : [perm]).some((c) => can(me, c));
}

function staffSections(): NavSection[] {
  return [
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
        // Касса (бывш. Табло бухгалтера): подтверждение оплат + вкладки «Долги» и «Транзакции».
        {
          href: "/accounting",
          label: "Касса",
          icon: HandCoins,
          perm: ["payments.confirm", "reports.view", "payments.view"],
        },
        // Единый пост: машины и вагоны вместе — лайв-этапы и моноблок отгрузки.
        { href: "/shipping", label: "Пост погрузки", icon: Truck, perm: ["shipping.view", "train.view"] },
        { href: "/monoblock", label: "Моноблок", icon: ScanLine, perm: "shipping.load" },
        { href: "/warehouse", label: "Склад", icon: Boxes, perm: "warehouse.view" },
        { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
        { href: "/stores", label: "Магазины", icon: Store, perm: "clients.view" },
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
        // Сотрудники и роли живут на одном экране вкладками — раскрывающаяся
        // группа «Доступы» ради двух пунктов только прятала их на клик глубже.
        {
          href: "/management/employees",
          label: "Сотрудники",
          icon: Settings,
          perm: ["employees.view", "rbac.view"],
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

// Активен только самый специфичный из совпавших пунктов: без этого на
// /portal/orders/new горели бы и «Новый заказ», и «Мои заказы» (/portal/orders).
function findActiveHref(sections: NavSection[], pathname: string): string | undefined {
  return sections
    .flatMap((s) => s.items.flatMap((i) => (i.children ? i.children.map((c) => c.href) : (i.href ?? []))))
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
        "group flex min-h-10 items-center gap-3 rounded-xl px-3 py-2 text-[13px] transition-all duration-200",
        active
          ? "bg-[var(--sidebar-accent)] font-medium text-[var(--sidebar-accent-foreground)] shadow-[0_8px_22px_-14px_rgba(220,238,191,0.75)]"
          : "font-normal text-[var(--sidebar-muted)] hover:translate-x-0.5 hover:bg-white/[0.055] hover:text-[var(--sidebar-foreground)]",
      )}
    >
      <Icon className={cn("size-[18px] shrink-0 transition-colors", active && "stroke-[2.25]")} />
      <span className="truncate">{label}</span>
      {active && <span className="ml-auto size-1.5 rounded-full bg-[var(--sidebar-accent-foreground)]/60" />}
    </Link>
  );
}

function NavGroup({ item, activeHref }: { item: NavItem; activeHref?: string }) {
  const Icon = item.icon;
  const childActive = item.children!.some((c) => c.href === activeHref);
  const [open, setOpen] = useState(childActive);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cn(
          "flex min-h-10 w-full items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-all",
          childActive
            ? "text-[var(--sidebar-foreground)]"
            : "text-[var(--sidebar-muted)] hover:bg-white/[0.055] hover:text-[var(--sidebar-foreground)]",
        )}
      >
        <Icon className="size-[18px] shrink-0" />
        <span className="flex-1 text-left">{item.label}</span>
        {open ? <ChevronDown className="size-3.5 opacity-60" /> : <ChevronRight className="size-3.5 opacity-60" />}
      </button>
      {open && (
        <div className="ml-5 mt-1 flex flex-col gap-1 border-l border-[var(--sidebar-border)] pl-3">
          {item.children!.map((c) => {
            const active = c.href === activeHref;
            return (
              <Link
                key={c.href}
                href={c.href}
                className={cn(
                  "rounded-lg px-2.5 py-2 text-[12px] font-medium transition-colors",
                  active
                    ? "bg-[var(--sidebar-accent)] text-[var(--sidebar-accent-foreground)]"
                    : "text-[var(--sidebar-muted)] hover:bg-white/[0.055] hover:text-[var(--sidebar-foreground)]",
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
  const pathname = usePathname();
  const sections: NavSection[] = me.is_client
    ? PORTAL_SECTIONS
    : me.is_monoblock
      ? [{ title: "Работа", items: [{ href: "/monoblock", label: "Моноблок", icon: ScanLine }] }]
      : staffSections();
  const visible = sections
    .map((s) => ({
      ...s,
      items: s.items
        .map((i) =>
          i.children
            ? { ...i, children: i.children.filter((c) => hasNavPerm(me, c.perm) && (!c.superuser || me.is_superuser)) }
            : i,
        )
        .filter((i) => hasNavPerm(me, i.perm) && (!i.children || i.children.length > 0)),
    }))
    .filter((s) => s.items.length > 0);

  const activeHref = findActiveHref(visible, pathname);
  const initials = me.username.slice(0, 2).toUpperCase();

  return (
    <>
      {/* профиль вверху */}
      <div className="flex min-h-[76px] items-center gap-3 border-b border-[var(--sidebar-border)] px-4 py-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-[14px] bg-white shadow-[0_8px_26px_-14px_rgba(255,255,255,0.9)]">
          <Image
            src="/logo-mark.png"
            alt="ASYL-LTD"
            width={30}
            height={30}
            className="size-7 object-contain"
            priority
          />
        </span>
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[14px] font-extrabold tracking-[0.03em] text-[var(--sidebar-foreground)]">
            ASYL-LTD
          </div>
          <div className="mt-0.5 truncate text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--sidebar-muted)]">
            {me.is_client ? "Кабинет клиента" : me.is_monoblock ? me.monoblock_name : "Мельничный комплекс"}
          </div>
        </div>
      </div>

      {/* навигация по группам */}
      <nav className="flex flex-1 flex-col gap-6 overflow-y-auto px-3 py-5" onClick={onNavigate}>
        {visible.map((section) => (
          <div key={section.title} className="flex flex-col gap-0.5">
            <div className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[var(--sidebar-muted)]/70">
              {section.title}
            </div>
            {section.items.map((item) =>
              item.children ? (
                <NavGroup key={item.label} item={item} activeHref={activeHref} />
              ) : (
                <NavLeaf
                  key={item.href}
                  href={item.href!}
                  label={item.label}
                  icon={item.icon}
                  active={item.href === activeHref}
                />
              ),
            )}
          </div>
        ))}
      </nav>

      {/* футер */}
      <div className="flex min-h-14 items-center justify-between border-t border-[var(--sidebar-border)] px-4 py-3 text-[10px] text-[var(--sidebar-muted)]">
        <span className="flex items-center gap-2">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-[#7fc98f] opacity-35" />
            <span className="relative size-2 rounded-full bg-[#68b77b]" />
          </span>
          {initials} · В сети
        </span>
        <span className="rounded-full border border-[var(--sidebar-border)] px-2 py-0.5">v1.0</span>
      </div>
    </>
  );
}

export function Sidebar({ me, mobileOpen = false, onClose }: { me: Me; mobileOpen?: boolean; onClose?: () => void }) {
  const pathname = usePathname();
  const mobilePanelRef = useRef<HTMLElement>(null);
  const mobileCloseRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Закрываем мобильную панель при смене маршрута.
  useEffect(() => {
    onClose?.();
  }, [onClose, pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = requestAnimationFrame(() => mobileCloseRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
        return;
      }
      if (event.key !== "Tab" || !mobilePanelRef.current) return;
      const focusable = Array.from(
        mobilePanelRef.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.closest("[inert]"));
      const first = focusable[0] ?? mobilePanelRef.current;
      const last = focusable.at(-1) ?? mobilePanelRef.current;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      const restoreTarget = restoreFocusRef.current;
      restoreFocusRef.current = null;
      if (restoreTarget?.isConnected && !restoreTarget.matches(":disabled")) {
        restoreTarget.focus();
      }
    };
  }, [mobileOpen, onClose]);

  return (
    <>
      {/* десктоп: постоянный сайдбар */}
      <aside
        data-tour="nav"
        className="hidden w-[260px] shrink-0 flex-col border-r border-[var(--sidebar-border)] bg-[var(--sidebar)] text-[var(--sidebar-foreground)] lg:flex"
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
          className={cn(
            "absolute inset-0 bg-black/60 backdrop-blur-[2px] transition-opacity",
            mobileOpen ? "opacity-100" : "opacity-0",
          )}
          onClick={onClose}
        />
        <aside
          ref={mobilePanelRef}
          role="dialog"
          aria-modal="true"
          aria-label="Меню навигации"
          tabIndex={-1}
          className={cn(
            "absolute inset-y-0 left-0 flex w-[280px] max-w-[86vw] flex-col border-r border-[var(--sidebar-border)] bg-[var(--sidebar)] pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] text-[var(--sidebar-foreground)] shadow-2xl transition-transform duration-300",
            mobileOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <button
            ref={mobileCloseRef}
            type="button"
            onClick={onClose}
            className="absolute right-3 top-[calc(1rem+env(safe-area-inset-top))] z-10 flex size-11 items-center justify-center rounded-xl text-[var(--sidebar-muted)] hover:bg-white/10 hover:text-[var(--sidebar-foreground)]"
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
