"use client";
import { useState } from "react";
import { Video, VideoOff, LayoutGrid, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface CameraFeed {
  id: number;
  name: string;
  /** HLS/MP4 stream URL. When null → shows "не подключена" placeholder. */
  url?: string | null;
}

// Заглушка списка камер цеха. Позже сюда подставляются реальные URL потоков
// (rtsp→hls), и плеер заработает без изменения разметки.
const DEFAULT_CAMERAS: CameraFeed[] = [
  { id: 1, name: "Въезд / весы", url: null },
  { id: 2, name: "Зона загрузки", url: null },
  { id: 3, name: "Ворота", url: null },
  { id: 4, name: "Склад", url: null },
];

function CameraTile({ cam, big = false }: { cam: CameraFeed; big?: boolean }) {
  return (
    <div className={cn(
      "relative overflow-hidden rounded-lg border bg-[var(--secondary)]",
      big ? "aspect-video" : "aspect-video"
    )}>
      {cam.url ? (
        <video
          src={cam.url}
          autoPlay muted playsInline controls
          className="h-full w-full bg-black object-cover"
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-[var(--muted-foreground)]">
          <VideoOff className={big ? "size-10" : "size-6"} />
          <span className="text-xs">Камера не подключена</span>
        </div>
      )}
      {/* overlay-метка */}
      <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-md bg-black/55 px-2 py-1 text-xs font-medium text-white">
        <span className={cn(
          "size-1.5 rounded-full",
          cam.url ? "bg-[var(--success)] animate-pulse" : "bg-[var(--muted-foreground)]"
        )} />
        {cam.name}
      </div>
    </div>
  );
}

export function CameraWall({ cameras = DEFAULT_CAMERAS }: { cameras?: CameraFeed[] }) {
  const [mode, setMode] = useState<"single" | "grid">("single");
  const [activeId, setActiveId] = useState(cameras[0]?.id ?? 1);
  const active = cameras.find((c) => c.id === activeId) ?? cameras[0];

  return (
    <div className="rounded-xl border bg-[var(--card)] p-4 shadow-sm">
      {/* панель управления */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Video className="size-4 text-[var(--muted-foreground)]" />
          Трансляция камер
        </div>
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          <button
            onClick={() => setMode("single")}
            className={cn(
              "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
              mode === "single"
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            )}
          >
            <Maximize2 className="size-3.5" /> Одна
          </button>
          <button
            onClick={() => setMode("grid")}
            className={cn(
              "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
              mode === "grid"
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            )}
          >
            <LayoutGrid className="size-3.5" /> Сетка
          </button>
        </div>
      </div>

      {mode === "single" ? (
        <div className="flex flex-col gap-3">
          <CameraTile cam={active} big />
          {/* выбор камеры */}
          <div className="flex flex-wrap gap-2">
            {cameras.map((c) => (
              <button
                key={c.id}
                onClick={() => setActiveId(c.id)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  c.id === activeId
                    ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
                )}
              >
                {c.name}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {cameras.map((c) => <CameraTile key={c.id} cam={c} />)}
        </div>
      )}
    </div>
  );
}
