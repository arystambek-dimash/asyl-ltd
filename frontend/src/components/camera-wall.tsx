"use client";
import { useCallback, useEffect, useState } from "react";
import { Video, VideoOff, LayoutGrid, Maximize2, Circle } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { CameraStream } from "@/components/camera-stream";

export interface CameraFeed {
  id: number;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam1..cam8). */
  src: string;
}

function CameraTile({
  cam,
  ready,
  big = false,
  onOnline,
}: {
  cam: CameraFeed;
  ready: boolean;
  big?: boolean;
  onOnline: (id: number, online: boolean) => void;
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
    <div className="group relative overflow-hidden rounded-lg border bg-[var(--secondary)] aspect-video">
      {ready && (
        <CameraStream src={cam.src} onStateChange={handleState}
          className="absolute inset-0 h-full w-full bg-black object-cover" />
      )}
      {!online && (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-[var(--muted-foreground)]">
          <VideoOff className={big ? "size-10" : "size-6"} />
          <span className={big ? "text-sm" : "text-[11px]"}>Нет сигнала</span>
        </div>
      )}
      <div className="absolute left-2.5 top-2.5 flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-[11px] font-medium text-white">
        <span className={cn(
          "size-1.5 rounded-full",
          online ? "bg-[var(--success)] animate-pulse" : "bg-red-500"
        )} />
        {cam.zone}
      </div>
      {online && (
        <div className="absolute right-2.5 top-2.5 flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
          <Circle className="size-1.5 fill-current" /> LIVE
        </div>
      )}
    </div>
  );
}

export function CameraWall() {
  const { data } = useApi<CameraFeed[]>("/cameras/");
  const cameras = data ?? [];
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState<number | null>(null);
  const [tokenReady, setTokenReady] = useState(false);
  const [onlineIds, setOnlineIds] = useState<Set<number>>(new Set());

  // cookie-доступ к потокам go2rtc; без неё nginx отдаст 403
  useEffect(() => {
    api.post("/cameras/token/")
      .then(() => setTokenReady(true))
      .catch(() => setTokenReady(false));
  }, []);

  const handleOnline = useCallback((id: number, online: boolean) => {
    setOnlineIds((prev) => {
      if (prev.has(id) === online) return prev;
      const next = new Set(prev);
      if (online) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const active = cameras.find((c) => c.id === activeId) ?? cameras[0];

  return (
    <div className="flex h-full flex-col rounded-xl border bg-[var(--card)] p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Video className="size-4 text-[var(--muted-foreground)]" />
          Видеонаблюдение
          <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]">
            {onlineIds.size}/{cameras.length} онлайн
          </span>
        </div>
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          <button onClick={() => setMode("grid")}
            className={cn("flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
              mode === "grid" ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
            <LayoutGrid className="size-3.5" /> Сетка
          </button>
          <button onClick={() => setMode("single")}
            className={cn("flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
              mode === "single" ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
            <Maximize2 className="size-3.5" /> Одна
          </button>
        </div>
      </div>

      {cameras.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-[var(--muted-foreground)]">
          <VideoOff className="size-8" />
          <span className="text-sm">Камеры недоступны</span>
          <span className="text-xs">NVR не в сети или потоки ещё не настроены</span>
        </div>
      ) : mode === "grid" ? (
        <div className="grid flex-1 grid-cols-2 gap-3 xl:grid-cols-3">
          {cameras.map((c) => (
            <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline} />
          ))}
        </div>
      ) : active ? (
        <div className="flex flex-1 flex-col gap-3">
          <CameraTile key={active.id} cam={active} ready={tokenReady} big onOnline={handleOnline} />
          <div className="flex flex-wrap gap-2">
            {cameras.map((c) => (
              <button key={c.id} onClick={() => setActiveId(c.id)}
                className={cn("rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  c.id === active.id ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]")}>
                {c.zone}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
