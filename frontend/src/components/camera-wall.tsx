"use client";
import { useState } from "react";
import { Video, VideoOff, LayoutGrid, Maximize2, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

export interface CameraFeed {
  id: number;
  name: string;
  zone: string;
  /** HLS/MP4 stream URL. When null → shows "не подключена" placeholder. */
  url?: string | null;
}

// Камеры цеха. Позже сюда подставляются реальные URL потоков (rtsp→hls),
// и плеер заработает без изменения разметки.
export const WAREHOUSE_CAMERAS: CameraFeed[] = [
  { id: 1, name: "Камера 1", zone: "Въезд / весы", url: null },
  { id: 2, name: "Камера 2", zone: "Зона загрузки", url: null },
  { id: 3, name: "Камера 3", zone: "Ворота", url: null },
  { id: 4, name: "Камера 4", zone: "Склад", url: null },
  { id: 5, name: "Камера 5", zone: "Производство", url: null },
  { id: 6, name: "Камера 6", zone: "Двор", url: null },
];

function CameraTile({ cam, big = false }: { cam: CameraFeed; big?: boolean }) {
  const online = !!cam.url;
  return (
    <div className="group relative overflow-hidden rounded-lg border bg-[var(--secondary)] aspect-video">
      {online ? (
        <video src={cam.url!} autoPlay muted playsInline controls
          className="h-full w-full bg-black object-cover" />
      ) : (
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

export function CameraWall({ cameras = WAREHOUSE_CAMERAS }: { cameras?: CameraFeed[] }) {
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState(cameras[0]?.id ?? 1);
  const active = cameras.find((c) => c.id === activeId) ?? cameras[0];
  const onlineCount = cameras.filter((c) => c.url).length;

  return (
    <div className="flex h-full flex-col rounded-xl border bg-[var(--card)] p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Video className="size-4 text-[var(--muted-foreground)]" />
          Видеонаблюдение
          <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]">
            {onlineCount}/{cameras.length} онлайн
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

      {mode === "grid" ? (
        <div className="grid flex-1 grid-cols-2 gap-3 xl:grid-cols-3">
          {cameras.map((c) => <CameraTile key={c.id} cam={c} />)}
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-3">
          <CameraTile cam={active} big />
          <div className="flex flex-wrap gap-2">
            {cameras.map((c) => (
              <button key={c.id} onClick={() => setActiveId(c.id)}
                className={cn("rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  c.id === activeId ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]")}>
                {c.zone}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
