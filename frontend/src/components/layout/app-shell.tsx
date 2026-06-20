"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

export function AppShell({
  title,
  children,
  portal = false,
}: {
  title: string;
  children: React.ReactNode;
  portal?: boolean;
}) {
  const { me, loading, loadMe } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!me) loadMe();
  }, [me, loadMe]);

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
    if (!loading && me) {
      if (portal && !me.is_client) router.replace("/dashboard");
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
      <Sidebar me={me} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar me={me} title={title} />
        <main className="flex-1 overflow-y-auto bg-[var(--background)] p-8">
          <div className="animate-fade-up">{children}</div>
        </main>
      </div>
    </div>
  );
}
