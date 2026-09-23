"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowDownUp, Camera, Crosshair, Plus, RefreshCw, RotateCcw, ScanSearch, Trash2, Video } from "lucide-react";
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, blobApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  COUNT_LINE_ID,
  defaultCountingLine,
  MAX_VERIFICATION_LINES,
  nextVerificationLine,
  VERIFICATION_LINE_NAME_MAX,
  verificationLineColor,
  type LineDirection,
  type NormalizedLine,
  type VerificationLine,
} from "@/lib/camera-counting-line";

export { defaultCountingLine, validCountingLine } from "@/lib/camera-counting-line";
export type { LineDirection, NormalizedLine, VerificationLine } from "@/lib/camera-counting-line";

/** «Подключение…» that never ends must not leave the operator without a picture. */
export const LIVE_FALLBACK_MS = 6_000;

const DIRECTIONS: Array<{ value: LineDirection; label: string; hint: string }> = [
  { value: "any", label: "В обе стороны", hint: "Считать любое пересечение" },
  { value: "up", label: "Снизу вверх", hint: "Только движение вверх" },
  { value: "down", label: "Сверху вниз", hint: "Только движение вниз" },
  { value: "positive", label: "Сторона +", hint: "По нормали линии" },
  { value: "negative", label: "Сторона −", hint: "Против нормали линии" },
];

/**
 * Live stream by default; the camera PC's latest still frame as a fallback.
 * The automatic fallback keeps the stream running until a frame has actually
 * arrived, and fires once: after that the operator chooses the source.
 */
function useStillFrame(src: string) {
  const [mode, setMode] = useState<"live" | "still">("live");
  const [online, setOnline] = useState(false);
  const [autoFallback, setAutoFallback] = useState(true);
  const [url, setUrl] = useState<string | null>(null);
  const [takenAt, setTakenAt] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const urlRef = useRef<string | null>(null);
  const onlineRef = useRef(false);

  /** A stream only reports changes, so a freshly mounted one starts offline. */
  const reportOnline = useCallback((value: boolean) => {
    onlineRef.current = value;
    setOnline(value);
  }, []);

  const enter = useCallback(
    (next: "live" | "still") => {
      reportOnline(false);
      setMode(next);
    },
    [reportOnline],
  );

  /** True when this request is still current and delivered a frame. */
  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const response = await api.get<Blob>(`/cameras/${encodeURIComponent(src)}/counting-line/frame`, {
        responseType: "blob",
        timeout: 15_000,
      });
      if (id !== requestId.current) return false;
      const next = URL.createObjectURL(response.data);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = next;
      setUrl(next);
      setTakenAt(new Date());
      return true;
    } catch (cause) {
      const message = await blobApiError(cause);
      if (id === requestId.current) setError(message || "Не удалось получить кадр камеры.");
      return false;
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [src]);

  const showStill = useCallback(() => {
    setAutoFallback(false);
    enter("still");
    void load();
  }, [enter, load]);

  const showLive = useCallback(() => {
    requestId.current += 1;
    setLoading(false);
    setError("");
    setAutoFallback(false);
    enter("live");
  }, [enter]);

  useEffect(() => {
    if (mode !== "live" || online || !autoFallback) return;
    const timer = setTimeout(() => {
      setAutoFallback(false);
      void load().then((loaded) => {
        // A stream that connected meanwhile wins; a failure stays a hint.
        if (loaded && !onlineRef.current) enter("still");
      });
    }, LIVE_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [mode, online, autoFallback, load, enter]);

  useEffect(
    () => () => {
      requestId.current += 1;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    },
    [],
  );

  return {
    mode,
    online,
    autoFallback,
    url,
    takenAt,
    loading,
    error,
    reportOnline,
    showStill,
    showLive,
    refresh: load,
  };
}

export function CameraLineEditor({
  src,
  line,
  direction,
  ready,
  disabled = false,
  verificationLines,
  verificationSupported,
  onLineChange,
  onDirectionChange,
  onVerificationLinesChange,
}: {
  src: string;
  line: NormalizedLine;
  direction: LineDirection;
  ready: boolean;
  disabled?: boolean;
  verificationLines: VerificationLine[];
  /** False on an AI service that cannot store verification lines yet. */
  verificationSupported: boolean;
  onLineChange: (line: NormalizedLine) => void;
  onDirectionChange: (direction: LineDirection) => void;
  onVerificationLinesChange: (lines: VerificationLine[]) => void;
}) {
  const [activeId, setActiveId] = useState(COUNT_LINE_ID);
  const frame = useStillFrame(src);
  const { online } = frame;
  const headingId = useId();
  const live = frame.mode === "live";
  const active = verificationLines.find((item) => item.id === activeId);
  const canAdd = !disabled && verificationSupported && verificationLines.length < MAX_VERIFICATION_LINES;

  const replaceLine = (id: string, change: Partial<VerificationLine>) =>
    onVerificationLinesChange(verificationLines.map((item) => (item.id === id ? { ...item, ...change } : item)));

  const addLine = () => {
    const created = nextVerificationLine(verificationLines, line);
    onVerificationLinesChange([...verificationLines, created]);
    setActiveId(created.id);
  };

  const removeLine = (id: string) => {
    onVerificationLinesChange(verificationLines.filter((item) => item.id !== id));
    if (activeId === id) setActiveId(COUNT_LINE_ID);
  };

  let status = online ? "Живое видео" : "Подключение…";
  if (!live) status = frame.loading ? "Загрузка кадра…" : frame.url ? "Снимок кадра" : "Нет кадра";

  return (
    <div className="space-y-4">
      <div className="group/line relative aspect-video overflow-hidden rounded-xl bg-[#111318] shadow-[0_20px_55px_-24px_rgba(15,23,42,.8)]">
        {live && ready && (
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
        <CameraCountingLineOverlay
          line={line}
          direction={direction}
          verificationLines={verificationLines}
          activeLineId={activeId}
          editable
          disabled={disabled}
          onLineChange={onLineChange}
          onVerificationLineChange={(id, next) => replaceLine(id, { line: next })}
          onActiveLineChange={setActiveId}
        />

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
          <Crosshair className="size-4 shrink-0" style={{ color: active ? undefined : "#7dd3fc" }} />
          <span className="truncate">
            {active
              ? `«${active.name.trim() || active.id}»: проведите линию или перетащите её точки`
              : "Проведите основную линию или перетащите её точки"}
          </span>
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
          <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {frame.error}
          </p>
        ))}

      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <label className="block">
          <span className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-semibold">
            <span className="flex items-center gap-2">
              <ArrowDownUp className="size-4 text-sky-600" /> Направление подсчёта
            </span>
            <span className="text-xs font-normal text-[var(--muted-foreground)]">только для основной линии</span>
          </span>
          <select
            value={direction}
            disabled={disabled}
            onChange={(event) => onDirectionChange(event.target.value as LineDirection)}
            className="h-11 w-full rounded-lg border bg-[var(--background)] px-3.5 text-sm outline-none transition focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 disabled:opacity-60"
          >
            {DIRECTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label} — {item.hint}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={() => onLineChange(defaultCountingLine())}
          className="h-11"
        >
          <RotateCcw className="size-4" /> По центру
        </Button>
      </div>

      <section aria-labelledby={headingId} className="rounded-xl border p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-[1_1_16rem]">
            <h3 id={headingId} className="flex items-center gap-2 text-sm font-semibold">
              <ScanSearch className="size-4 text-amber-500" /> Линии проверки
              <span className="rounded-full bg-[var(--accent)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]">
                {verificationLines.length} из {MAX_VERIFICATION_LINES}
              </span>
            </h3>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              Мешок классифицируется на каждой линии проверки; счёт — только по основной линии.
            </p>
          </div>
          <Button type="button" size="sm" variant="outline" disabled={!canAdd} onClick={addLine}>
            <Plus className="size-4" /> Добавить линию проверки
          </Button>
        </div>

        {!verificationSupported ? (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Этот AI-сервис ещё не поддерживает линии проверки. Обновите AI-сервис, чтобы включить проверку мешков.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            <li>
              <button
                type="button"
                aria-pressed={!active}
                onClick={() => setActiveId(COUNT_LINE_ID)}
                className={cn(
                  "flex h-11 w-full items-center gap-3 rounded-lg border px-3 text-left text-sm transition hover:bg-[var(--accent)]",
                  !active && "border-sky-400 ring-2 ring-sky-500/15",
                )}
              >
                <span className="h-1 w-5 shrink-0 rounded-full bg-sky-400" />
                <span className="whitespace-nowrap font-medium">Основная линия</span>
                <span className="min-w-0 truncate text-xs text-[var(--muted-foreground)]">подсчёт мешков</span>
              </button>
            </li>
            {verificationLines.map((item, index) => {
              const label = item.name.trim() || item.id;
              const selected = item.id === activeId;
              return (
                <li
                  key={item.id}
                  className={cn(
                    "flex items-center gap-1.5 rounded-lg border p-1.5",
                    selected && "border-sky-400 ring-2 ring-sky-500/15",
                  )}
                >
                  <button
                    type="button"
                    aria-label={`Выбрать линию «${label}»`}
                    aria-pressed={selected}
                    onClick={() => setActiveId(item.id)}
                    className="flex size-8 shrink-0 items-center justify-center rounded-md transition hover:bg-[var(--accent)]"
                  >
                    <span className="h-1 w-5 rounded-full" style={{ backgroundColor: verificationLineColor(index) }} />
                  </button>
                  <Input
                    value={item.name}
                    maxLength={VERIFICATION_LINE_NAME_MAX}
                    disabled={disabled}
                    aria-label={`Название линии проверки ${index + 1}`}
                    onFocus={() => setActiveId(item.id)}
                    onChange={(event) => replaceLine(item.id, { name: event.target.value })}
                    className="h-9 min-w-0 flex-1"
                  />
                  <button
                    type="button"
                    aria-label={`Удалить линию «${label}»`}
                    disabled={disabled}
                    onClick={() => removeLine(item.id)}
                    className="flex size-8 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
