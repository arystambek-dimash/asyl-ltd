"use client";

import { useState } from "react";
import { ArrowDownUp, Crosshair, RotateCcw } from "lucide-react";
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { defaultCountingLine, type LineDirection, type NormalizedLine } from "@/lib/camera-counting-line";

export { defaultCountingLine, validCountingLine } from "@/lib/camera-counting-line";
export type { LineDirection, NormalizedLine } from "@/lib/camera-counting-line";

const DIRECTIONS: Array<{ value: LineDirection; label: string; hint: string }> = [
  { value: "any", label: "В обе стороны", hint: "Считать любое пересечение" },
  { value: "up", label: "Снизу вверх", hint: "Только движение вверх" },
  { value: "down", label: "Сверху вниз", hint: "Только движение вниз" },
  { value: "positive", label: "Сторона +", hint: "По нормали линии" },
  { value: "negative", label: "Сторона −", hint: "Против нормали линии" },
];

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
  const [online, setOnline] = useState(false);

  return (
    <div className="space-y-4">
      <div
        className={cn(
          "group/line relative aspect-video overflow-hidden rounded-xl bg-[#111318] shadow-[0_20px_55px_-24px_rgba(15,23,42,.8)]",
        )}
      >
        {ready && (
          <CameraStream src={src} onStateChange={setOnline} className="absolute inset-0 h-full w-full object-contain" />
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/30 via-transparent to-black/15" />
        <CameraCountingLineOverlay
          line={line}
          direction={direction}
          editable
          disabled={disabled}
          onLineChange={onLineChange}
        />

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
