"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { hasAuthTokens, isRefreshTokenRemoval, isRefreshTokenReplacement } from "@/lib/api";
import { OnboardingTour } from "@/components/onboarding-tour";
import { Sidebar } from "./sidebar";
import { Topbar, type TopbarBack } from "./topbar";

const INITIAL_SESSION_RETRY_MS = 2_000;
const MAX_SESSION_RETRY_MS = 30_000;

export function AppShell({
  title,
  section,
  description,
  children,
  portal = false,
  tabs,
  actions,
  back,
  trailing,
  footer,
}: {
  title: React.ReactNode;
  section?: string;
  description?: string;
  children: React.ReactNode;
  portal?: boolean;
  tabs?: React.ReactNode;
  actions?: React.ReactNode;
  back?: TopbarBack;
  trailing?: React.ReactNode;
  /** Панель под контентом (навигация кассы на телефоне): вне прокрутки и вне анимации контента. */
  footer?: React.ReactNode;
}) {
  const { me, loading, loadMe, refreshMe, logout, syncExternalSession } = useAuth();
  const router = useRouter();
  const [navOpen, setNavOpen] = useState(false);
  const sessionRetryDelay = useRef(INITIAL_SESSION_RETRY_MS);
  const closeNav = useCallback(() => setNavOpen(false), []);
  const openNav = useCallback(() => setNavOpen(true), []);

  useEffect(() => {
    if (!me) loadMe();
  }, [me, loadMe]);

  // A saved session may be temporarily unverifiable while the API restarts.
  // Keep the credentials, retry with bounded backoff, and avoid leaving a
  // visible tab on the loading screen until the user happens to refocus it.
  useEffect(() => {
    if (me || !hasAuthTokens()) {
      sessionRetryDelay.current = INITIAL_SESSION_RETRY_MS;
      return;
    }
    if (loading) return;

    const delay = sessionRetryDelay.current;
    const timer = window.setTimeout(() => {
      sessionRetryDelay.current = Math.min(delay * 2, MAX_SESSION_RETRY_MS);
      void loadMe();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [loadMe, loading, me]);

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
    const refreshSession = () => {
      if (me) refreshMe();
      else if (hasAuthTokens()) {
        sessionRetryDelay.current = INITIAL_SESSION_RETRY_MS;
        loadMe();
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshSession();
    };
    window.addEventListener("focus", onVisible);
    window.addEventListener("online", refreshSession);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      window.removeEventListener("online", refreshSession);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [loadMe, me, refreshMe]);

  useEffect(() => {
    if (!loading && !me && !hasAuthTokens()) router.replace("/login");
    if (!loading && me && portal !== me.is_client) router.replace(homeFor(me));
  }, [loading, me, portal, router]);

  if (loading || !me)
    return (
      <div className="flex h-dvh items-center justify-center text-sm text-[var(--muted-foreground)]">Загрузка…</div>
    );

  return (
    // dvh, не vh: на телефоне 100vh — высота со свёрнутой панелью браузера, и нижняя панель уезжала бы за край.
    <div className="flex h-dvh overflow-hidden">
      {/* Обучение по системе: первый вход + повторно по кнопке «?» */}
      {!me.is_client && <OnboardingTour me={me} />}
      <Sidebar me={me} mobileOpen={navOpen} onClose={closeNav} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar
          me={me}
          title={title}
          section={section}
          tabs={tabs}
          actions={actions}
          onMenu={openNav}
          back={back}
          trailing={trailing}
        />
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
        {/* `position: fixed` внутри .animate-fade-up цеплялся бы за его transform — панель живёт вне main. */}
        {footer}
      </div>
    </div>
  );
}
