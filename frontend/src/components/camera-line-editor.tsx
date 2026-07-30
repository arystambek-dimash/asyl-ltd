"use client";

import { useMemo, useRef, useState } from "react";
import { ArrowDownUp, Crosshair, RotateCcw } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type LineDirection = "any" | "up" | "down" | "positive" | "negative";

export interface NormalizedLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const DEFAULT_LINE: NormalizedLine = { x1: 0.12, y1: 0.5, x2: 0.88, y2: 0.5 };
const WIDTH = 1000;
const HEIGHT = 562.5;

const DIRECTIONS: Array<{ value: LineDirection; label: string; hint: string }> = [
  { value: "any", label: "В обе стороны", hint: "Считать любое пересечение" },
  { value: "up", label: "Снизу вверх", hint: "Только движение вверх" },
  { value: "down", label: "Сверху вниз", hint: "Только движение вниз" },
  { value: "positive", label: "Сторона +", hint: "По нормали линии" },
  { value: "negative", label: "Сторона −", hint: "Против нормали линии" },
];

function clamp(value: number) {
  return Math.max(0, Math.min(1, value));
}

function tooShort(line: NormalizedLine) {
  return Math.hypot(line.x2 - line.x1, line.y2 - line.y1) < 0.01;
}

function directionalArrow(line: NormalizedLine, direction: LineDirection) {
  const start = { x: line.x1 * WIDTH, y: line.y1 * HEIGHT };
  const end = { x: line.x2 * WIDTH, y: line.y2 * HEIGHT };
  const middle = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  let vx = (-dy / length) * 52;
  let vy = (dx / length) * 52;

  if (direction === "up") [vx, vy] = [0, -52];
  if (direction === "down") [vx, vy] = [0, 52];
  if (direction === "negative") [vx, vy] = [-vx, -vy];

  return {
    x1: middle.x - vx / 2,
    y1: middle.y - vy / 2,
    x2: middle.x + vx / 2,
    y2: middle.y + vy / 2,
  };
}

export function defaultCountingLine() {
  return { ...DEFAULT_LINE };
}

export function validCountingLine(line: NormalizedLine) {
  return !tooShort(line);
}

export function CameraLineEditor({
  src,
  line,
  direction,
  ready,
  disabled = false,
  onLineChange,
  onDirectionChange,
}: {
  src: string;
  line: NormalizedLine;
  direction: LineDirection;
  ready: boolean;
  disabled?: boolean;
  onLineChange: (line: NormalizedLine) => void;
  onDirectionChange: (direction: LineDirection) => void;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<"start" | "end" | "draw" | null>(null);
  const [online, setOnline] = useState(false);
  const arrow = useMemo(() => directionalArrow(line, direction), [line, direction]);

  const pointAt = (clientX: number, clientY: number) => {
    const rect = surfaceRef.current?.getBoundingClientRect();
    if (!rect) return null;
    return {
      x: clamp((clientX - rect.left) / rect.width),
      y: clamp((clientY - rect.top) / rect.height),
      rect,
    };
  };

  const begin = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);

    const distance = (x: number, y: number) =>
      Math.hypot((point.x - x) * point.rect.width, (point.y - y) * point.rect.height);
    if (distance(line.x1, line.y1) <= 24) {
      setDragging("start");
    } else if (distance(line.x2, line.y2) <= 24) {
      setDragging("end");
    } else {
      setDragging("draw");
      onLineChange({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
    }
  };

  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging || disabled) return;
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    if (dragging === "start") onLineChange({ ...line, x1: point.x, y1: point.y });
    else onLineChange({ ...line, x2: point.x, y2: point.y });
  };

  const finish = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(null);
  };

  return (
    <div className="space-y-4">
      <div
        ref={surfaceRef}
        onPointerDown={begin}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
        className={cn(
          "group/line relative aspect-video touch-none select-none overflow-hidden rounded-xl bg-[#111318] shadow-[0_20px_55px_-24px_rgba(15,23,42,.8)]",
          disabled ? "cursor-wait" : "cursor-crosshair",
        )}
      >
        {ready && (
          <CameraStream src={src} onStateChange={setOnline} className="absolute inset-0 h-full w-full object-cover" />
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-black/15" />

        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="pointer-events-none absolute inset-0 h-full w-full"
          aria-hidden="true"
        >
          <defs>
            <filter id="line-glow" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="6" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <marker
              id="line-arrow"
              viewBox="0 0 10 10"
              refX="7"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#f8fafc" />
            </marker>
          </defs>
          <line
            x1={line.x1 * WIDTH}
            y1={line.y1 * HEIGHT}
            x2={line.x2 * WIDTH}
            y2={line.y2 * HEIGHT}
            stroke="rgba(15,23,42,.75)"
            strokeWidth="13"
            strokeLinecap="round"
          />
          <line
            x1={line.x1 * WIDTH}
            y1={line.y1 * HEIGHT}
            x2={line.x2 * WIDTH}
            y2={line.y2 * HEIGHT}
            stroke="#38bdf8"
            strokeWidth="6"
            strokeLinecap="round"
            filter="url(#line-glow)"
          />
          <line
            {...arrow}
            stroke="#f8fafc"
            strokeWidth="4"
            strokeLinecap="round"
            markerStart={direction === "any" ? "url(#line-arrow)" : undefined}
            markerEnd="url(#line-arrow)"
          />
          {([line.x1, line.x2] as const).map((x, index) => {
            const y = index === 0 ? line.y1 : line.y2;
            return (
              <g key={index}>
                <circle cx={x * WIDTH} cy={y * HEIGHT} r="18" fill="rgba(15,23,42,.7)" />
                <circle cx={x * WIDTH} cy={y * HEIGHT} r="11" fill="#f8fafc" stroke="#38bdf8" strokeWidth="5" />
              </g>
            );
          })}
        </svg>

        <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-full border border-white/15 bg-black/55 px-3 py-1.5 text-xs font-medium text-white shadow-lg backdrop-blur-md">
          <span className={cn("size-2 rounded-full", online ? "bg-emerald-400" : "bg-amber-400")} />
          {online ? "Живое видео" : "Подключение…"}
        </div>
        <div className="pointer-events-none absolute bottom-3 left-3 flex items-center gap-2 rounded-lg border border-white/15 bg-black/60 px-3 py-2 text-xs text-white/90 backdrop-blur-md">
          <Crosshair className="size-4 text-sky-300" />
          Проведите новую линию или перетащите её точки
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <label className="block">
          <span className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
            <ArrowDownUp className="size-4 text-sky-600" /> Направление подсчёта
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
    </div>
  );
}
