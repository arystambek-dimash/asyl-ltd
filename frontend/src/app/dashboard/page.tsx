"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { BarChart3, Maximize2, RefreshCw, Video } from "lucide-react";
import { TodayBoard } from "@/components/dashboard/today-board";
import { TvScreen } from "@/components/dashboard/tv-screen";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Select } from "@/components/ui/select";
import { Tabs } from "@/components/ui/tabs";
import { can } from "@/lib/can";
import { forgetStoredChoice, readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import { useDashboardMetrics } from "@/lib/use-dashboard-metrics";
import { useAuth } from "@/store/auth";

const CameraWall = dynamic(() => import("@/components/camera-wall").then((module) => module.CameraWall), {
  ssr: false,
  loading: () => (
    <div className="h-72 animate-pulse rounded-[22px] border bg-[var(--muted)]/45" aria-label="Загрузка камер" />
  ),
});

const DASHBOARD_VIEWS = [
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "cameras", label: "Камеры", icon: Video },
] as const;
type DashboardView = (typeof DASHBOARD_VIEWS)[number]["key"];
const VIEW_STORAGE_KEY = "dashboard:view";
// Режим ТВ помнит браузер: телевизор после перезагрузки снова показывает сводку.
const TV_STORAGE_KEY = "dashboard:tv";

function AnalyticsView({ userId }: { userId: number }) {
  const m = useDashboardMetrics();
  const tvKey = userChoiceKey(TV_STORAGE_KEY, userId);
  const [tv, setTv] = useState(false);
  useEffect(() => {
    setTv(readStoredChoice(tvKey) === "1");
  }, [tvKey]);

  const openTv = () => {
    setTv(true);
    storeChoice(tvKey, "1");
    // Полный экран браузер даёт только по клику; отказ — не ошибка, сводка и так поверх меню.
    document.documentElement.requestFullscreen?.().catch(() => undefined);
  };
  const closeTv = () => {
    setTv(false);
    forgetStoredChoice(tvKey);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
  };

  if (!m.canOrders && !m.canStock && !m.canFinance) {
    return (
      <section className="flex min-h-72 flex-col items-center justify-center gap-2 rounded-[22px] border border-dashed text-center">
        <BarChart3 className="size-8 text-[var(--muted-foreground)]/40" />
        <p className="font-semibold">Сводка появится, когда роли дадут доступ к заказам, складу или отчётам</p>
        <p className="max-w-md text-sm text-[var(--muted-foreground)]">
          Рабочие разделы есть в меню. Если раздела не хватает, попросите администратора добавить его в роль.
        </p>
      </section>
    );
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1480px]">
      <TodayBoard
        m={m}
        size="page"
        actions={
          <>
            {m.warehouses.length > 1 && m.warehouseId !== null && (
              <Select
                aria-label="Склад"
                className="h-11 w-auto rounded-xl"
                value={String(m.warehouseId)}
                onChange={(event) => m.chooseWarehouse(Number(event.target.value))}
              >
                {m.warehouses.map((warehouse) => (
                  <option key={warehouse.id} value={warehouse.id}>
                    {warehouse.name}
                  </option>
                ))}
              </Select>
            )}
            <button
              type="button"
              onClick={openTv}
              className="inline-flex h-11 items-center gap-2 rounded-xl border bg-[var(--card)] px-4 text-sm font-medium transition hover:border-[var(--input)]"
            >
              <Maximize2 className="size-4" />
              Режим ТВ
            </button>
            <button
              type="button"
              aria-label="Обновить данные"
              title="Обновить данные"
              onClick={() => void m.reload()}
              className="flex size-11 items-center justify-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] transition hover:border-[var(--input)] hover:text-[var(--foreground)]"
            >
              <RefreshCw className="size-4" />
            </button>
          </>
        }
      />
      {tv && <TvScreen m={m} onExit={closeTv} />}
    </div>
  );
}

export default function DashboardPage() {
  return (
    <RequirePerm perm="dashboard.view" title="Главная">
      <DashboardPageInner />
    </RequirePerm>
  );
}

function DashboardPageInner() {
  const { me } = useAuth();
  const userId = me?.id;
  const showCameras = can(me, "monoblock.view");
  const [view, setView] = useState<DashboardView | null>(null);

  // До загрузки профиля ключ не известен: чужой выбор на общем устройстве
  // не подхватываем, а экран всё равно закрыт загрузкой AppShell.
  useEffect(() => {
    if (!userId) return;
    const saved = readStoredChoice(userChoiceKey(VIEW_STORAGE_KEY, userId));
    setView(saved === "cameras" ? "cameras" : "analytics");
  }, [userId]);

  const changeView = (nextView: DashboardView) => {
    setView(nextView);
    storeChoice(userChoiceKey(VIEW_STORAGE_KEY, userId), nextView);
  };
  const activeView = showCameras ? view : "analytics";

  return (
    <AppShell
      title="Главная"
      tabs={
        showCameras &&
        activeView && (
          <Tabs tabs={[...DASHBOARD_VIEWS]} active={activeView} onChange={(key) => changeView(key as DashboardView)} />
        )
      }
    >
      {userId && activeView && (activeView === "analytics" ? <AnalyticsView userId={userId} /> : <CameraWall />)}
    </AppShell>
  );
}
