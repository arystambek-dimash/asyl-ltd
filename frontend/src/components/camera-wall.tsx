"use client";
import { useCallback, useEffect, useState } from "react";
import { Grid2x2, RectangleHorizontal, Video, VideoOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { CameraStream, ensureCameraStreamToken } from "@/components/camera-stream";

const CAMERA_REFRESH_MS = 30 * 1000;
const RETRY_MAX_MS = 60 * 1000;

/** Камера из живого инвентаря сети (бэкенд строит его из ai_service). */
export interface CameraFeed {
  /** Стабильный ключ: kind + MAC (не меняется при перетасовке каналов NVR). */
  id: string;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam2, cam_8c26); null у locked-камер. */
  src: string | null;
  kind: "nvr-channel" | "direct" | "locked";
  /** Живость источника по данным инвентаря (у locked всегда false). */
  online: boolean;
  /** Пояснение для locked: обнаружена, но пароль неизвестен. */
  note?: string;
}

/** Камеры, у которых есть поток для просмотра (locked не играют). */
export function playableCameras(cams: CameraFeed[] | null | undefined) {
  return (cams ?? []).filter((c): c is CameraFeed & { src: string } => !!c.src);
}

function CameraTile({
  cam,
  ready,
  onOnline,
  onClick,
  active = false,
}: {
  // Только играбельные камеры (с потоком); недоступные не показываем.
  cam: CameraFeed & { src: string };
  ready: boolean;
  onOnline: (id: string, online: boolean) => void;
  onClick?: () => void;
  active?: boolean;
}) {
  const [online, setOnline] = useState(false);
  const handleState = useCallback(
    (v: boolean) => {
      setOnline(v);
      onOnline(cam.id, v);
    },
    [cam.id, onOnline]
  );

  return (
    <div
      onClick={onClick}
      className={cn(
        "group relative aspect-video overflow-hidden rounded-lg bg-[#1c1c1e]",
        onClick && "cursor-pointer",
        active && "ring-2 ring-[var(--ring)]",
      )}
    >
      {ready && (
        <CameraStream src={cam.src} onStateChange={handleState}
          className="absolute inset-0 h-full w-full object-cover" />
      )}

      {!online && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-white/30">
          <VideoOff className="size-5" />
          <span className="text-[11px]">Нет сигнала</span>
        </div>
      )}

      {/* Нижний скрим с именем камеры и статусом — как в UniFi Protect */}
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-2.5 pb-1.5 pt-5">
        <span className="text-xs font-medium text-white drop-shadow-sm">{cam.zone}</span>
        <span className={cn("size-1.5 rounded-full", online ? "bg-emerald-400" : "bg-white/30")} />
      </div>
    </div>
  );
}

export function CameraWall() {
  const [cameras, setCameras] = useState<CameraFeed[]>([]);
  const [loading, setLoading] = useState(true);
  const playable = playableCameras(cameras);
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tokenReady, setTokenReady] = useState(false);
  const [onlineIds, setOnlineIds] = useState<Set<string>>(new Set());

  // Живой инвентарь: при ошибке сохраняем последнюю успешную выборку, а
  // повторяем запрос с ограниченным бэкоффом. После восстановления сети и
  // возврата во вкладку не ждём следующего таймера.
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let rerun = false;
    let failures = 0;

    const schedule = (delay: number) => {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void refresh();
      }, delay);
    };

    const refresh = async () => {
      if (disposed) return;
      if (inFlight) {
        rerun = true;
        return;
      }
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      inFlight = true;
      let nextDelay = CAMERA_REFRESH_MS;
      try {
        const response = await api.get<CameraFeed[]>("/cameras/", { timeout: 10_000 });
        if (!Array.isArray(response.data)) throw new Error("invalid camera inventory");
        if (!disposed) setCameras(response.data);
        failures = 0;
      } catch {
        failures += 1;
        nextDelay = Math.min(RETRY_MAX_MS, 1000 * 2 ** Math.min(failures - 1, 6));
      } finally {
        inFlight = false;
        if (!disposed) setLoading(false);
        if (rerun) {
          rerun = false;
          void refresh();
        } else {
          schedule(nextDelay);
        }
      }
    };

    const refreshNow = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", refreshNow);
    window.addEventListener("online", refreshNow);
    void refresh();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshNow);
      window.removeEventListener("online", refreshNow);
    };
  }, []);

  // cookie-доступ к потокам go2rtc; без неё nginx отдаст 403. Ошибка не
  // оставляет стену навсегда пустой: повторяем с тем же capped backoff.
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;

    const acquire = async () => {
      try {
        await ensureCameraStreamToken();
        if (!disposed) {
          failures = 0;
          setTokenReady(true);
        }
      } catch {
        if (disposed) return;
        setTokenReady(false);
        failures += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures - 1, 5));
        timer = setTimeout(() => void acquire(), delay);
      }
    };

    const acquireNow = () => {
      if (document.visibilityState !== "visible" || tokenReady) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void acquire();
    };
    document.addEventListener("visibilitychange", acquireNow);
    window.addEventListener("online", acquireNow);
    void acquire();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", acquireNow);
      window.removeEventListener("online", acquireNow);
    };
  }, [tokenReady]);

  // Удалённая из нового инвентаря камера не должна оставаться в счётчике.
  useEffect(() => {
    const ids = new Set(cameras.map((camera) => camera.id));
    setOnlineIds((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [cameras]);

  const handleOnline = useCallback((id: string, online: boolean) => {
    setOnlineIds((prev) => {
      if (prev.has(id) === online) return prev;
      const next = new Set(prev);
      if (online) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  // Показываем только играбельные камеры; недоступные (locked) не выводим вовсе.
  const active = playable.find((c) => c.id === activeId) ?? playable[0];

  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <div className="flex items-center gap-2.5 border-b px-4 py-3">
        <Video className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-sm font-semibold">Камеры</span>
        <span className="text-xs text-[var(--muted-foreground)]">
          {onlineIds.size} из {playable.length} онлайн
        </span>
        {playable.length > 1 && (
          <div className="ml-auto flex items-center gap-0.5">
            <button
              onClick={() => setMode("grid")}
              title="Сетка"
              className={cn("rounded-md p-1.5 transition-colors",
                mode === "grid" ? "bg-[var(--accent)] text-[var(--foreground)]"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
              <Grid2x2 className="size-4" />
            </button>
            <button
              onClick={() => setMode("single")}
              title="Одна камера"
              className={cn("rounded-md p-1.5 transition-colors",
                mode === "single" ? "bg-[var(--accent)] text-[var(--foreground)]"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
              <RectangleHorizontal className="size-4" />
            </button>
          </div>
        )}
      </div>

      <div className="p-4">
        {playable.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-[var(--muted-foreground)]">
            <VideoOff className="size-6" />
            <div className="text-sm font-medium">{loading ? "Загрузка…" : "Камеры недоступны"}</div>
            {!loading && <div className="text-xs">NVR не в сети или потоки ещё не настроены</div>}
          </div>
        ) : mode === "grid" || playable.length === 1 ? (
          <div className={cn(
            "grid gap-3",
            playable.length === 1 ? "mx-auto max-w-2xl grid-cols-1" :
            playable.length <= 4 ? "grid-cols-2" : "grid-cols-2 lg:grid-cols-3",
          )}>
            {playable.map((c) => (
              <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                onClick={playable.length > 1
                  ? () => { setActiveId(c.id); setMode("single"); } : undefined} />
            ))}
          </div>
        ) : active ? (
          <div className="flex flex-col gap-3">
            <CameraTile key={active.id} cam={active} ready={tokenReady} onOnline={handleOnline} />
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
              {playable.map((c) => (
                <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                  active={c.id === active.id} onClick={() => setActiveId(c.id)} />
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
