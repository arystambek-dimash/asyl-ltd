"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  Check,
  Clock3,
  LockKeyhole,
  PackageCheck,
  Radio,
  Settings2,
  ShieldCheck,
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
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { AiCountingSession, MonoblockCameraSettings, Order } from "@/lib/types";
import { useAiCounter } from "@/lib/use-ai-counter";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const SESSION_POLL_MS = 3_000;
// Заказы/камеры/настройки меняются редко — не гоняем полный список заказов
// каждые 3 секунды на экране, который висит открытым весь день.
const SLOW_POLL_MS = 30_000;

function CameraChoice({
  camera,
  checked,
  onToggle,
}: {
  camera: CameraFeed & { src: string };
  checked: boolean;
  onToggle: () => void;
}) {
  const [streamOnline, setStreamOnline] = useState(false);

  return (
    <button type="button" onClick={onToggle} aria-pressed={checked}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? "border-blue-400 bg-blue-50 shadow-[0_10px_28px_rgba(59,104,210,0.15)] ring-2 ring-blue-500/20"
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
      )}>
      <div className="relative aspect-video overflow-hidden bg-[#151821]">
        <CameraStream src={camera.src} onStateChange={setStreamOnline}
          className="absolute inset-0 size-full object-cover transition duration-300 group-hover:scale-[1.02]" />

        {!streamOnline && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-slate-950/75 text-white/45">
            <VideoOff className="size-5" />
            <span className="text-[11px]">Нет изображения</span>
          </div>
        )}

        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/65 to-transparent px-3 pb-8 pt-2.5">
          <span className="flex items-center gap-1.5 rounded-full bg-black/35 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
            {streamOnline ? "ОНЛАЙН" : "НЕТ СИГНАЛА"}
          </span>
          <span className={cn(
            "flex size-7 items-center justify-center rounded-full border backdrop-blur-md transition",
            checked
              ? "border-blue-300 bg-blue-600 text-white"
              : "border-white/35 bg-black/25 text-transparent",
          )}>
            <Check className="size-4" />
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3 px-3.5 py-3">
        <span className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-xl",
          checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
        )}>
          <Camera className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
          <span className="mt-0.5 block truncate text-[11px] text-slate-400">{camera.name}</span>
        </span>
      </div>
    </button>
  );
}

function CameraSettingsButton({
  cameras,
  settings,
  reload,
}: {
  cameras: (CameraFeed & { src: string })[];
  settings: MonoblockCameraSettings | null;
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setSelected(settings?.camera_sources ?? []);
    setError("");
    setOpen(true);
  }

  function toggle(source: string) {
    setSelected((current) => current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source]);
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.put("/cameras/monoblock-settings/", { camera_sources: selected });
      await reload();
      setOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button variant="outline" className="h-10 rounded-xl bg-white" onClick={show}>
        <Settings2 className="size-4" /> Камеры моноблока
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] tabular-nums text-slate-500">
          {settings?.camera_sources.length ?? 0}
        </span>
      </Button>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Настройка администратора"
        title="Камеры моноблока"
        description="Отметьте камеры, которые оператор сможет назначать заказам."
        className="max-w-xl"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Отмена</Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить список"}
            </Button>
          </>
        )}>
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-sm text-blue-900">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-blue-600" />
          <p>Изменение применяется для всех устройств. Активные отгрузки продолжат работу, но новые увидят только выбранные камеры.</p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            return (
              <CameraChoice key={camera.id} camera={camera} checked={checked}
                onToggle={() => toggle(camera.src)} />
            );
          })}
        </div>

        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </Modal>
    </>
  );
}

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
    try {
      await ai.stop();
    } catch {
      // ошибка уже показана через ai.error — карточку всё равно обновляем
    } finally {
      onStopped();
    }
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
  const { me } = useAuth();
  const { data: orders, error, reload: reloadOrders } = useApi<Order[]>("/orders/");
  const { data: cameras, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const { data: sessions, reload: reloadSessions } = useApi<AiCountingSession[]>("/cameras/ai/sessions/");
  const { data: cameraSettings, reload: reloadCameraSettings } = useApi<MonoblockCameraSettings>(
    "/cameras/monoblock-settings/",
  );
  const playable = useMemo(() => playableCameras(cameras), [cameras]);
  const monoblockCameras = useMemo(() => {
    const allowed = new Set(cameraSettings?.camera_sources ?? []);
    return playable.filter((camera) => allowed.has(camera.src));
  }, [cameraSettings?.camera_sources, playable]);

  useEffect(() => {
    const refreshSessions = () => {
      if (document.hidden) return;
      void reloadSessions();
    };
    const refreshRest = () => {
      if (document.hidden) return;
      void reloadOrders();
      void reloadCameras();
      void reloadCameraSettings();
    };
    const refreshAll = () => { refreshSessions(); refreshRest(); };
    const fast = setInterval(refreshSessions, SESSION_POLL_MS);
    const slow = setInterval(refreshRest, SLOW_POLL_MS);
    document.addEventListener("visibilitychange", refreshAll);
    window.addEventListener("online", refreshAll);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
      document.removeEventListener("visibilitychange", refreshAll);
      window.removeEventListener("online", refreshAll);
    };
  }, [reloadCameraSettings, reloadCameras, reloadOrders, reloadSessions]);

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
          {can(me, "rbac.manage") && (
            <div className="flex items-center justify-end">
              <CameraSettingsButton cameras={playable} settings={cameraSettings}
                reload={reloadCameraSettings} />
            </div>
          )}
          <ShipmentLauncher
            orders={startable}
            cameras={monoblockCameras}
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
