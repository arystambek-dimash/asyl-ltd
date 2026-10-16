"use client";

import { useId, useState } from "react";
import { ArrowDownUp, Plus, RotateCcw, ScanSearch, Trash2 } from "lucide-react";
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { CameraFrameStage } from "@/components/camera-frame-stage";
import { CameraVisibilityMask } from "@/components/camera-visibility-mask";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import type { VehicleRoiConfig } from "@/lib/types";

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
  disabled = false,
  verificationLines,
  verificationSupported,
  visibilityZone,
  onLineChange,
  onDirectionChange,
  onVerificationLinesChange,
}: {
  src: string;
  line: NormalizedLine;
  direction: LineDirection;
  disabled?: boolean;
  verificationLines: VerificationLine[];
  /** False on an AI service that cannot store verification lines yet. */
  verificationSupported: boolean;
  /** Зона видимости камеры: вне неё AI не видит, линии там бесполезны. */
  visibilityZone?: VehicleRoiConfig | null;
  onLineChange: (line: NormalizedLine) => void;
  onDirectionChange: (direction: LineDirection) => void;
  onVerificationLinesChange: (lines: VerificationLine[]) => void;
}) {
  const [activeId, setActiveId] = useState(COUNT_LINE_ID);
  const headingId = useId();
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

  return (
    <div className="space-y-4">
      <CameraFrameStage
        src={src}
        hintColor={active ? undefined : "#7dd3fc"}
        hint={
          active
            ? `«${active.name.trim() || active.id}»: проведите линию или перетащите её точки`
            : "Проведите основную линию или перетащите её точки"
        }
      >
        <CameraVisibilityMask zone={visibilityZone} />
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
      </CameraFrameStage>

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
