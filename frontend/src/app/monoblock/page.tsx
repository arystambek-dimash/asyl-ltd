"use client";

import { useEffect, useMemo } from "react";
import {
  Camera,
  Clock3,
  LockKeyhole,
  PackageCheck,
  Radio,
  Square,
  UserRound,
  VideoOff,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { CameraStream } from "@/components/camera-stream";
import { RequirePerm } from "@/components/require-perm";
import { ShipmentLauncher } from "@/components/shipping/shipment-launcher";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { api } from "@/lib/api";
import type { AiCountingSession, Order } from "@/lib/types";
import { useAiCounter } from "@/lib/use-ai-counter";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";

const SESSION_POLL_MS = 3_000;

function SessionCard({
  session,
  camera,
  onStopped,
}: {
  session: AiCountingSession;
  camera?: CameraFeed & { src: string };
  onStopped: () => void;
}) {
  const ai = useAiCounter(session.camera, session.order_id, true);
  const live = ai.status?.running;
  const total = ai.status?.total ?? session.last_status?.total ?? 0;
  const canStop = ai.status?.can_stop ?? session.can_stop;
  const stream = ai.status?.stream ?? (live ? `${session.camera}ai` : camera?.src);

  async function stop() {
    await ai.stop();
    onStopped();
  }

  return (
    <article className="group overflow-hidden rounded-[22px] border border-slate-200/80 bg-white shadow-[0_12px_38px_rgba(44,65,103,0.07)] transition hover:-translate-y-0.5 hover:shadow-[0_18px_48px_rgba(44,65,103,0.11)]">
      <div className="relative aspect-[16/8] overflow-hidden bg-[#172033]">
        {stream ? (
          <CameraStream
            src={stream}
            className="absolute inset-0 size-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
            <VideoOff className="size-6" />
            <span className="text-xs">Поток запускается</span>
          </div>
        )}
        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/55 to-transparent px-4 pb-8 pt-3">
          <span className="flex items-center gap-2 rounded-full bg-black/35 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-2 rounded-full", live ? "animate-pulse bg-emerald-400" : "bg-amber-400")} />
            {live ? "СЧИТЫВАНИЕ" : "ЗАПУСК"}
          </span>
          <span className="rounded-full bg-black/35 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur-md">
            {camera?.zone || session.camera}
          </span>
        </div>
        <div className="absolute bottom-3 right-3 rounded-2xl border border-white/20 bg-slate-950/65 px-4 py-2 text-right text-white backdrop-blur-lg">
          <div className="text-[10px] uppercase tracking-[0.14em] text-white/55">мешков</div>
          <div className="text-3xl font-bold tabular-nums leading-none">{total}</div>
        </div>
      </div>

      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <PackageCheck className="size-4 shrink-0 text-blue-600" />
              <h3 className="truncate text-[15px] font-bold text-slate-800">
                Заказ #{session.order_id} · {session.order_client_name || "Без клиента"}
              </h3>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500">
              <span className="flex items-center gap-1.5">
                <UserRound className="size-3.5" /> {session.started_by_name}
              </span>
              <span className="flex items-center gap-1.5">
                <Clock3 className="size-3.5" /> {formatDateTime(session.started_at)}
              </span>
              <span className="flex items-center gap-1.5">
                <Camera className="size-3.5" /> {session.camera}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-4">
          {canStop ? (
            <Button
              variant="outline"
              className="h-10 w-full rounded-xl border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700"
              disabled={ai.busy}
              onClick={() => void stop()}
            >
              <Square className="size-3.5 fill-current" /> Остановить отгрузку
            </Button>
          ) : (
            <div className="flex items-center justify-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[12px] text-slate-500">
              <LockKeyhole className="size-3.5" /> Остановить может {session.started_by_name} или администратор
            </div>
          )}
          {ai.error && <p className="mt-2 text-center text-xs text-red-600">{ai.error}</p>}
        </div>
      </div>
    </article>
  );
}

function MonoblockPageInner() {
  const { data: orders, error, reload: reloadOrders } = useApi<Order[]>("/orders/");
  const { data: cameras, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const { data: sessions, reload: reloadSessions } = useApi<AiCountingSession[]>("/cameras/ai/sessions/");
  const playable = useMemo(() => playableCameras(cameras), [cameras]);

  useEffect(() => {
    const refresh = () => {
      if (document.hidden) return;
      void reloadOrders();
      void reloadCameras();
      void reloadSessions();
    };
    const timer = setInterval(refresh, SESSION_POLL_MS);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("online", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("online", refresh);
    };
  }, [reloadCameras, reloadOrders, reloadSessions]);

  const sessionOrderIds = new Set((sessions ?? []).map((session) => session.order_id));
  const startable = (orders ?? []).filter((order) => {
    if (sessionOrderIds.has(order.id)) return false;
    if (order.transport_type === "train") return ["confirmed", "loading"].includes(order.status);
    return ["arrived", "loading"].includes(order.status);
  });

  async function start(order: Order, camera: CameraFeed & { src: string }) {
    await api.post(`/orders/${order.id}/loading-camera/`, { camera: camera.src });
    if (order.transport_type === "train" && order.status === "confirmed") {
      await api.post(`/orders/${order.id}/train/`, { action: "start" });
    }
    await api.post(`/cameras/${camera.src}/ai/`, { order_id: order.id }, {
      params: { order_id: order.id },
    });
    await Promise.all([reloadOrders(), reloadSessions()]);
  }

  return (
    <AppShell title="Моноблок" section="Работа">
      {error && !orders ? (
        <ErrorAlert message={error} onRetry={reloadOrders} />
      ) : (
        <div className="flex flex-col gap-7">
          <ShipmentLauncher
            orders={startable}
            cameras={playable}
            busyCameras={(sessions ?? []).map((session) => session.camera)}
            activeSessionCount={sessions?.length ?? 0}
            onStart={start}
          />

          <section>
            <div className="mb-4 flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <Radio className="size-5" />
              </span>
              <div>
                <h2 className="text-[20px] font-bold tracking-tight text-slate-800">Активные отгрузки</h2>
                <p className="text-[12px] text-slate-400">Каждая сессия закреплена за отдельной камерой</p>
              </div>
              <span className="ml-auto rounded-full border bg-white px-3 py-1 text-[12px] font-semibold text-slate-600 shadow-sm">
                {sessions?.length ?? 0} активн.
              </span>
            </div>

            {!sessions?.length ? (
              <div className="flex min-h-48 flex-col items-center justify-center rounded-[22px] border border-dashed border-slate-200 bg-slate-50/70 text-center">
                <span className="flex size-14 items-center justify-center rounded-full bg-white text-slate-300 shadow-sm">
                  <Radio className="size-6" />
                </span>
                <p className="mt-3 text-sm font-semibold text-slate-600">Активных сессий пока нет</p>
                <p className="mt-1 text-xs text-slate-400">Выберите заказ и камеру выше, чтобы начать.</p>
              </div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
                {sessions.map((session) => (
                  <SessionCard
                    key={session.id}
                    session={session}
                    camera={playable.find((camera) => camera.src === session.camera)}
                    onStopped={() => void reloadSessions()}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}

export default function MonoblockPage() {
  return (
    <RequirePerm perm="shipping.load" title="Моноблок">
      <MonoblockPageInner />
    </RequirePerm>
  );
}
