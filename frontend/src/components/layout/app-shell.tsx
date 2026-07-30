"use client";
import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { isRefreshTokenRemoval, isRefreshTokenReplacement } from "@/lib/api";
import { OnboardingTour } from "@/components/onboarding-tour";
import { LoaderCircle } from "lucide-react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

export function AppShell({
  title,
  section,
  description,
  children,
  portal = false,
  tabs,
  actions,
}: {
  title: string;
  section?: string;
  description?: string;
  children: React.ReactNode;
  portal?: boolean;
  tabs?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const { me, loading, loadMe, refreshMe, logout, syncExternalSession } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const closeNav = useCallback(() => setNavOpen(false), []);
  const openNav = useCallback(() => setNavOpen(true), []);

  useEffect(() => {
    if (!me) loadMe();
  }, [me, loadMe]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      // `storageArea` is null for synthetic events, so keep those testable while
      // ignoring a similarly named sessionStorage key in real browsers.
      if (event.storageArea && event.storageArea !== window.localStorage) return;
      if (isRefreshTokenRemoval(event)) logout();
      else if (isRefreshTokenReplacement(event)) void syncExternalSession();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [logout, syncExternalSession]);

  // Права могли поменять, пока вкладка была в фоне — тихо перечитываем.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshMe();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshMe]);

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
    if (!loading && me) {
      if (portal && !me.is_client) router.replace(homeFor(me));
      if (!portal && me.is_client) router.replace("/portal/catalog");
      if (me.is_monoblock && pathname !== "/monoblock") router.replace("/monoblock");
    }
  }, [loading, me, pathname, portal, router]);

  if (loading || !me)
    return (
      <div className="app-workspace flex h-dvh items-center justify-center">
        <div className="flex items-center gap-3 rounded-2xl border bg-[var(--card)] px-5 py-4 text-sm font-medium shadow-card">
          <LoaderCircle className="size-5 animate-spin text-[var(--ring)]" />
          Загружаем рабочее пространство
        </div>
      </div>
    );

  return (
    <div className="flex h-dvh overflow-hidden bg-[var(--sidebar)]">
      {/* Обучение по системе: первый вход + повторно по кнопке «?» */}
      {!me.is_client && !me.is_monoblock && <OnboardingTour me={me} />}
      <Sidebar me={me} mobileOpen={navOpen} onClose={closeNav} />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--workspace)]">
        <Topbar me={me} title={title} section={section} tabs={tabs} actions={actions} onMenu={openNav} />
        {actions && (
          <div className="no-scrollbar flex shrink-0 items-center justify-end gap-2 overflow-x-auto border-b bg-[var(--card)] px-3 py-2 sm:hidden">
            {actions}
          </div>
        )}
        {/* На телефоне вкладкам нет места в навбаре — отдельная строка под ним. */}
        {tabs && (
          <div className="no-scrollbar shrink-0 overflow-x-auto border-b bg-[var(--card)] px-4 sm:hidden [&>div]:border-b-0">
            {tabs}
          </div>
        )}
        <main className="app-workspace min-w-0 flex-1 overflow-y-auto px-3 pb-[calc(1rem+env(safe-area-inset-bottom))] pt-4 sm:px-6 sm:py-6 lg:px-8 lg:py-7">
          <div className="mx-auto w-full max-w-[1600px] animate-fade-up">
            {/* Заголовок уже показан в топбаре — здесь только пояснение,
                иначе название страницы дублируется и «режет глаза». */}
            {description && (
              <p className="mb-5 max-w-3xl text-[13px] leading-relaxed text-[var(--muted-foreground)] sm:mb-6 sm:text-sm">
                {description}
              </p>
            )}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
