"use client";
import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { isRefreshTokenRemoval, isRefreshTokenReplacement } from "@/lib/api";
import { OnboardingTour } from "@/components/onboarding-tour";
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
      <div className="flex h-screen items-center justify-center text-sm text-[var(--muted-foreground)]">Загрузка…</div>
    );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Обучение по системе: первый вход + повторно по кнопке «?» */}
      {!me.is_client && !me.is_monoblock && <OnboardingTour me={me} />}
      <Sidebar me={me} mobileOpen={navOpen} onClose={closeNav} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar me={me} title={title} section={section} tabs={tabs} actions={actions} onMenu={openNav} />
        {/* На телефоне вкладкам нет места в навбаре — отдельная строка под ним. */}
        {tabs && <div className="overflow-x-auto px-4 sm:hidden">{tabs}</div>}
        <main className="flex-1 overflow-y-auto bg-[var(--background)] px-4 py-5 sm:px-8 sm:py-7">
          <div className="animate-fade-up">
            {/* Заголовок уже показан в топбаре — здесь только пояснение,
                иначе название страницы дублируется и «режет глаза». */}
            {description && <p className="mb-6 max-w-2xl text-sm text-[var(--muted-foreground)]">{description}</p>}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
