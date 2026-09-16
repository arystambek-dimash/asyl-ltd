"use client";
import type { ReactNode } from "react";
import { Menu, CircleHelp, ChevronLeft } from "lucide-react";
import { NotificationBell } from "@/components/notification-bell";
import { CartButton } from "@/components/portal/cart-button";
import { TOUR_START_EVENT } from "@/components/onboarding-tour";
import { ProfileMenu } from "./profile-menu";
import type { Me } from "@/lib/types";

/** Кнопка «назад» вместо «☰» на подэкранах мобильных разделов. */
export interface TopbarBack {
  label: string;
  onClick: () => void;
}

export function Topbar({
  me,
  title,
  section,
  tabs,
  actions,
  onMenu,
  back,
  trailing,
}: {
  me: Me;
  /** Обычно строка; касса на телефоне ставит сюда кнопку выбора отдела. */
  title: ReactNode;
  section?: string;
  tabs?: ReactNode;
  actions?: ReactNode;
  onMenu?: () => void;
  back?: TopbarBack;
  /** Иконки справа от заголовка (перед темой и профилем), например фильтры экрана. */
  trailing?: ReactNode;
}) {
  const accountLabel = me.is_client ? "Клиент" : me.is_superuser ? "Администратор" : me.position || "Сотрудник";

  return (
    <header className="flex min-h-16 flex-wrap items-center gap-2 border-b px-4 py-2 sm:px-8 xl:h-16 xl:flex-nowrap xl:py-0">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {back ? (
          <button
            type="button"
            onClick={back.onClick}
            className="-ml-1 flex size-9 shrink-0 items-center justify-center rounded-md text-[var(--foreground)] hover:bg-[var(--secondary)] lg:hidden"
            aria-label={back.label}
          >
            <ChevronLeft className="size-5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={onMenu}
            className="-ml-1 flex size-9 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:bg-[var(--secondary)] lg:hidden"
            aria-label="Меню"
          >
            <Menu className="size-5" />
          </button>
        )}
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
          <div
            className="ml-4 hidden h-16 min-w-0 self-stretch overflow-x-auto sm:flex
            [&>div]:h-full [&>div]:border-b-0 [&_button]:h-full [&_button]:whitespace-nowrap"
          >
            {tabs}
          </div>
        )}
      </div>
      {actions && (
        <div className="order-3 flex w-full items-center justify-start overflow-x-auto pt-1 xl:order-none xl:w-auto xl:justify-end xl:overflow-visible xl:pt-0">
          {actions}
        </div>
      )}
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        {trailing}
        {!me.is_client && (
          <button
            onClick={() => window.dispatchEvent(new Event(TOUR_START_EVENT))}
            className="hidden size-8 items-center justify-center rounded-lg border text-[var(--muted-foreground)] hover:text-[var(--foreground)] sm:flex"
            title="Обучение по системе"
            aria-label="Обучение по системе"
          >
            <CircleHelp className="size-4" />
          </button>
        )}
        {me.is_client && <CartButton />}
        {me.is_client && <NotificationBell />}
        <ProfileMenu me={me} accountLabel={accountLabel} />
      </div>
    </header>
  );
}
