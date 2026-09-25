"use client";
import { ReactNode } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/store/auth";
import { canAny, type Perm } from "@/lib/can";
import { ShieldOff } from "lucide-react";

/**
 * Оборачивает страницу: если у текущего пользователя нет нужного права —
 * показывает заглушку «Нет доступа» вместо содержимого.
 * perm — строка или массив (нужно ЛЮБОЕ из прав); superuser — страница только
 * для владельца, права ролей не играют.
 */
export function RequirePerm({
  title = "Раздел",
  children,
  ...access
}: ({ perm: Perm; superuser?: never } | { superuser: true; perm?: never }) & {
  title?: string;
  children: ReactNode;
}) {
  const { me, loading } = useAuth();
  const allowed = access.superuser ? !!me?.is_superuser : canAny(me, access.perm);

  // Пока профиль грузится, AppShell сам показывает «Загрузка…» и содержимое не рисует.
  if (loading || !allowed) {
    return <AppShell title={title}>{loading ? null : <NoAccessCard />}</AppShell>;
  }
  return <>{children}</>;
}

/** Заглушка «Нет доступа». */
function NoAccessCard() {
  return (
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
  );
}
