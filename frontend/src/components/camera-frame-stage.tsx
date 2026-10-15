"use client";

import type { ReactNode } from "react";
import { Camera, Crosshair, RefreshCw, Video } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { useCameraStillFrame } from "@/lib/use-camera-still-frame";
import { cn } from "@/lib/utils";

/**
 * Кадр камеры для разметки суперпользователем: живое видео или снимок с ПК
 * камер, статус источника и подсказка. Слои разметки передаются `children` —
 * они соседи видео и выравниваются по его реальному боксу (useVideoBox).
 */
export function CameraFrameStage({
  src,
  hint,
  hintColor,
  children,
}: {
  src: string;
  hint: ReactNode;
  /** Цвет прицела подсказки; без него — цвет текста. */
  hintColor?: string;
  children?: ReactNode;
}) {
  const frame = useCameraStillFrame(src);
  const { online } = frame;
  const live = frame.mode === "live";

  let status = online ? "Живое видео" : "Подключение…";
  if (!live) status = frame.loading ? "Загрузка кадра…" : frame.url ? "Снимок кадра" : "Нет кадра";

  return (
    <>
      <div className="group/line relative aspect-video overflow-hidden rounded-xl bg-[#111318] shadow-[0_20px_55px_-24px_rgba(15,23,42,.8)]">
        {live && (
          <CameraStream
            src={src}
            onStateChange={frame.reportOnline}
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        {!live && frame.url && (
          // A blob: URL of an authenticated JPEG; next/image cannot optimize it.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            data-video-box-source
            src={frame.url}
            alt="Кадр камеры для разметки"
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-black/15" />
        {children}

        <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-full border border-white/15 bg-black/55 px-3 py-1.5 text-xs font-medium text-white shadow-lg backdrop-blur-md">
          <span
            className={cn(
              "size-2 rounded-full",
              (live ? online : !!frame.url && !frame.loading) ? "bg-emerald-400" : "bg-amber-400",
            )}
          />
          {status}
        </div>
        <div className="pointer-events-none absolute bottom-3 left-3 right-3 flex items-center gap-2 rounded-lg border border-white/15 bg-black/60 px-3 py-2 text-xs text-white/90 backdrop-blur-md sm:right-auto">
          <Crosshair className="size-4 shrink-0" style={{ color: hintColor }} />
          <span className="truncate">{hint}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {live ? (
          <Button type="button" size="sm" variant="outline" onClick={frame.showStill}>
            <Camera className="size-4" /> Показать кадр
          </Button>
        ) : (
          <>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={frame.loading}
              onClick={() => void frame.refresh()}
            >
              <RefreshCw className={cn("size-4", frame.loading && "animate-spin")} /> Обновить кадр
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={frame.showLive}>
              <Video className="size-4" /> Живое видео
            </Button>
          </>
        )}
        <span className="text-xs text-[var(--muted-foreground)]">
          {live
            ? frame.autoFallback && !online
              ? "Если видео не подключится, откроется последний кадр камеры."
              : ""
            : frame.takenAt
              ? `Кадр от ${frame.takenAt.toLocaleTimeString("ru-RU")}`
              : ""}
        </span>
      </div>
      {frame.error &&
        (live ? (
          // The automatic fallback found no frame: the stream keeps trying.
          <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            Снимок кадра недоступен: {frame.error}
          </p>
        ) : (
          <FormError message={frame.error} className="rounded-lg px-4 py-3" />
        ))}
    </>
  );
}
