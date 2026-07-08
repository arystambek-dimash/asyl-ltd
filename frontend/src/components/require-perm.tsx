"use client";
import { ReactNode } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ShieldOff } from "lucide-react";

/**
 * Оборачивает страницу: если у текущего пользователя нет нужного права —
 * показывает заглушку «Нет доступа» вместо содержимого.
 * perm — строка или массив (нужно ЛЮБОЕ из прав).
 * superuserOnly — раздел доступен только суперадмину (perm игнорируется).
 */
export function RequirePerm({ perm, superuserOnly = false, title = "Раздел", children }: {
  perm: string | string[];
  superuserOnly?: boolean;
  title?: string;
  children: ReactNode;
}) {
  const { me, loading } = useAuth();
  const codes = Array.isArray(perm) ? perm : [perm];
  const allowed = superuserOnly
    ? !!me?.is_superuser
    : !!me && codes.some((c) => can(me, c));

  if (loading) {
    return <AppShell title={title}><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;
  }
  if (!allowed) {
    return (
      <AppShell title={title}>
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <span className="flex size-12 items-center justify-center rounded-full bg-[var(--muted)]">
              <ShieldOff className="size-6 text-[var(--muted-foreground)]" />
            </span>
            <div className="text-lg font-semibold">Нет доступа</div>
            <p className="max-w-sm text-sm text-[var(--muted-foreground)]">
              У вас нет прав для просмотра этого раздела. Обратитесь к администратору.
            </p>
          </CardContent>
        </Card>
      </AppShell>
    );
  }
  return <>{children}</>;
}
