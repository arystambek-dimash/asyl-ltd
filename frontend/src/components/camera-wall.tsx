"use client";
import { useCallback, useEffect, useState } from "react";
import { Circle, LayoutGrid, Maximize2, VideoOff } from "lucide-react";
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

/* Уголки-«прицел» по краям видеотайла, как на операторском мониторе. */
function Brackets({ className }: { className?: string }) {
  const base = "pointer-events-none absolute size-3.5 border-[color:var(--warning)]/70";
  return (
    <div className={cn("pointer-events-none absolute inset-2 z-10 opacity-0 transition-opacity duration-300 group-hover:opacity-100", className)}>
      <span className={cn(base, "left-0 top-0 border-l-2 border-t-2")} />
      <span className={cn(base, "right-0 top-0 border-r-2 border-t-2")} />
      <span className={cn(base, "bottom-0 left-0 border-b-2 border-l-2")} />
      <span className={cn(base, "bottom-0 right-0 border-b-2 border-r-2")} />
    </div>
  );
}

function CameraTile({
  cam,
  ready,
  big = false,
  onOnline,
  onClick,
  active = false,
}: {
  cam: CameraFeed;
  ready: boolean;
  big?: boolean;
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
        "group relative overflow-hidden rounded-xl border bg-black/60 aspect-video",
        "border-white/10 transition-all duration-300",
        onClick && "cursor-pointer hover:border-[color:var(--warning)]/60 hover:shadow-[0_0_24px_-6px_var(--warning)]",
        active && "border-[color:var(--warning)]/80 shadow-[0_0_20px_-6px_var(--warning)]",
      )}
    >
      {ready && (
        <CameraStream src={cam.src} onStateChange={handleState}
          className="absolute inset-0 h-full w-full bg-black object-cover" />
      )}
      {online && <div className="cmd-scan absolute inset-0" />}
      <Brackets />

      {!online && (
        <div className="cmd-hatch flex h-full w-full flex-col items-center justify-center gap-2 text-white/35">
          <VideoOff className={big ? "size-10" : "size-6"} />
          <span className={cn("font-[family-name:var(--font-mono)] uppercase tracking-[0.2em]", big ? "text-xs" : "text-[10px]")}>
            нет сигнала
          </span>
        </div>
      )}

      <div className="absolute left-2.5 bottom-2.5 z-10 flex items-center gap-1.5 rounded-md bg-black/70 px-2 py-1 backdrop-blur-sm">
        <span className={cn("cmd-led size-1.5 rounded-full", online ? "bg-emerald-400 text-emerald-400" : "bg-red-500 text-red-500")} />
        <span className="font-[family-name:var(--font-mono)] text-[10px] font-semibold uppercase tracking-[0.14em] text-white/90">
          {cam.zone}
        </span>
      </div>

      {online && (
        <div className="absolute right-2.5 top-2.5 z-10 flex items-center gap-1.5 rounded bg-red-600/95 px-1.5 py-0.5 text-[10px] font-bold tracking-wider text-white">
          <Circle className="cmd-blink size-1.5 fill-current" /> LIVE
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
    <div className="flex flex-col gap-3">
      {/* Панель управления стеной */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className={cn("cmd-led size-2 rounded-full", onlineIds.size > 0 ? "bg-emerald-400 text-emerald-400" : "bg-red-500 text-red-500")} />
          <span className="font-[family-name:var(--font-mono)] text-[11px] font-semibold uppercase tracking-[0.22em] text-white/60">
            Видеонаблюдение
          </span>
          <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] font-bold tabular-nums text-[color:var(--warning)]">
            {onlineIds.size}/{cameras.length}
          </span>
        </div>
        <div className="flex items-center gap-0.5 rounded-lg border border-white/10 bg-white/5 p-0.5">
          {([["grid", LayoutGrid, "Сетка"], ["single", Maximize2, "Одна"]] as const).map(([m, Icon, label]) => (
            <button key={m} onClick={() => setMode(m)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 font-[family-name:var(--font-mono)] text-[11px] font-semibold uppercase tracking-wider transition-all",
                mode === m ? "bg-[color:var(--warning)] text-black shadow-[0_0_14px_-2px_var(--warning)]"
                  : "text-white/50 hover:text-white/90")}>
              <Icon className="size-3.5" /> {label}
            </button>
          ))}
        </div>
      </div>

      {cameras.length === 0 ? (
        <div className="cmd-hatch flex flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed border-white/15 py-14 text-white/40">
          <VideoOff className="size-8" />
          <span className="font-[family-name:var(--font-mono)] text-xs uppercase tracking-[0.25em]">Камеры недоступны</span>
          <span className="text-[11px] text-white/30">NVR не в сети или потоки ещё не настроены</span>
        </div>
      ) : mode === "grid" ? (
        <div className={cn(
          "grid gap-3",
          cameras.length === 1 ? "grid-cols-1 sm:grid-cols-2" :
          cameras.length <= 4 ? "grid-cols-2" : "grid-cols-2 xl:grid-cols-3",
        )}>
          {cameras.map((c, i) => (
            <div key={c.id} className="cmd-rise" style={{ animationDelay: `${0.08 * i + 0.15}s` }}>
              <CameraTile cam={c} ready={tokenReady} onOnline={handleOnline}
                onClick={() => { setActiveId(c.id); setMode("single"); }} />
            </div>
          ))}
        </div>
      ) : active ? (
        <div className="flex flex-col gap-3">
          <CameraTile key={active.id} cam={active} ready={tokenReady} big onOnline={handleOnline} />
          {cameras.length > 1 && (
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
              {cameras.map((c) => (
                <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                  active={c.id === active.id} onClick={() => setActiveId(c.id)} />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
