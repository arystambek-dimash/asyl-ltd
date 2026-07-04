"use client";
import { useCallback, useEffect, useState } from "react";
import { Grid2x2, RectangleHorizontal, Video, VideoOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { CameraStream } from "@/components/camera-stream";

export interface CameraFeed {
  id: number;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam1..camN). */
  src: string;
}

function CameraTile({
  cam,
  ready,
  onOnline,
  onClick,
  active = false,
}: {
  cam: CameraFeed;
  ready: boolean;
  onOnline: (id: number, online: boolean) => void;
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
  const { data, loading } = useApi<CameraFeed[]>("/cameras/");
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
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <div className="flex items-center gap-2.5 border-b px-4 py-3">
        <Video className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-sm font-semibold">Камеры</span>
        <span className="text-xs text-[var(--muted-foreground)]">
          {onlineIds.size} из {cameras.length} онлайн
        </span>
        {cameras.length > 1 && (
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
        {cameras.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-[var(--muted-foreground)]">
            <VideoOff className="size-6" />
            <div className="text-sm font-medium">{loading ? "Загрузка…" : "Камеры недоступны"}</div>
            {!loading && <div className="text-xs">NVR не в сети или потоки ещё не настроены</div>}
          </div>
        ) : mode === "grid" || cameras.length === 1 ? (
          <div className={cn(
            "grid gap-3",
            cameras.length === 1 ? "mx-auto max-w-2xl grid-cols-1" :
            cameras.length <= 4 ? "grid-cols-2" : "grid-cols-2 lg:grid-cols-3",
          )}>
            {cameras.map((c) => (
              <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                onClick={cameras.length > 1 ? () => { setActiveId(c.id); setMode("single"); } : undefined} />
            ))}
          </div>
        ) : active ? (
          <div className="flex flex-col gap-3">
            <CameraTile key={active.id} cam={active} ready={tokenReady} onOnline={handleOnline} />
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
              {cameras.map((c) => (
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
