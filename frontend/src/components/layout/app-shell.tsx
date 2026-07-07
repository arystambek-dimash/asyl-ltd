"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

export function AppShell({
  title,
  section,
  description,
  children,
  portal = false,
  actions,
}: {
  title: string;
  section?: string;
  description?: string;
  children: React.ReactNode;
  portal?: boolean;
  actions?: React.ReactNode;
}) {
  const { me, loading, loadMe } = useAuth();
  const router = useRouter();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (!me) loadMe();
  }, [me, loadMe]);

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
    if (!loading && me) {
      if (portal && !me.is_client) router.replace(homeFor(me));
      if (!portal && me.is_client) router.replace("/portal/catalog");
    }
  }, [loading, me, portal, router]);

  if (loading || !me)
    return (
      <div className="flex h-screen items-center justify-center text-sm text-[var(--muted-foreground)]">
        Загрузка…
      </div>
    );

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar me={me} mobileOpen={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar me={me} title={title} section={section} actions={actions}
          onMenu={() => setNavOpen(true)} />
        <main className="flex-1 overflow-y-auto bg-[var(--background)] px-4 py-5 sm:px-8 sm:py-7">
          <div className="animate-fade-up">
            {description && (
              <div className="mb-7">
                <h2 className="text-2xl font-bold tracking-tight">{title}</h2>
                <p className="mt-1.5 max-w-2xl text-sm text-[var(--muted-foreground)]">{description}</p>
              </div>
            )}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
