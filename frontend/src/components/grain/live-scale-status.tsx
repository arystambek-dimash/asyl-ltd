"use client";

import { Scale } from "lucide-react";
import type { TruckScalePreview } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn } from "@/lib/utils";

const TONNES_FORMATTER = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const DEFAULT_POLL_MS = 2_000;
const ERROR_POLL_MS = 5_000;
const MIN_POLL_MS = 1_000;
const MAX_POLL_MS = 10_000;

function pollInterval(data: TruckScalePreview | null, hasError: boolean): number {
  if (hasError) return ERROR_POLL_MS;
  const requested = data?.poll_after_ms;
  if (typeof requested !== "number" || !Number.isFinite(requested)) return DEFAULT_POLL_MS;
  return Math.min(MAX_POLL_MS, Math.max(MIN_POLL_MS, requested));
}

function weightInTonnes(weightKg: string | null): string | null {
  if (weightKg == null) return null;
  const value = Number(weightKg);
  if (!Number.isFinite(value) || value < 0) return null;
  return `${TONNES_FORMATTER.format(value / 1000)} т`;
}

type DisplayState = {
  value: string;
  label: string;
  tone: "ready" | "warning" | "offline";
};

function displayState(data: TruckScalePreview | null, loading: boolean, error: string): DisplayState {
  if (error) return { value: "—,— т", label: "Нет связи с CRM", tone: "offline" };
  if (!data) {
    return {
      value: "—,— т",
      label: loading ? "Подключение…" : "Нет данных",
      tone: "offline",
    };
  }

  const weight = weightInTonnes(data.weight_kg);
  if (data.state === "ready" && weight) {
    return { value: weight, label: data.capturable ? "Вес стабилен" : "Весы готовы", tone: "ready" };
  }
  if (data.state === "unstable" && weight) {
    return { value: `≈ ${weight}`, label: "Вес меняется", tone: "warning" };
  }

  const labels: Record<TruckScalePreview["state"], string> = {
    ready: "Некорректный вес",
    unstable: "Вес меняется",
    stale: "Нет свежих данных",
    disconnected: "Весы отключены",
    unavailable: "ПК весов недоступен",
    disabled: "Весы не настроены",
    malformed: "Ошибка данных весов",
    refreshing: "Обновление…",
  };
  return {
    value: "—,— т",
    label: labels[data.state],
    tone: data.state === "unstable" || data.state === "refreshing" ? "warning" : "offline",
  };
}

export function LiveScaleStatus({ active }: { active: boolean }) {
  const { data, loading, error, reload } = useApi<TruckScalePreview>(active ? "/truck-scale/reading/" : null);
  useVisiblePolling(reload, pollInterval(data, Boolean(error)), active);

  if (!active) return null;

  const display = displayState(data, loading, error);
  const label = `Автомобильные весы: ${display.value}, ${display.label}`;

  return (
    <div
      aria-label={label}
      title={display.label}
      className="flex h-9 min-w-[156px] shrink-0 items-center gap-2 rounded-md border border-[#26382a] bg-[#101511] px-2.5 shadow-inner"
    >
      <Scale
        aria-hidden="true"
        className={cn(
          "size-4 shrink-0",
          display.tone === "ready"
            ? "text-emerald-400"
            : display.tone === "warning"
              ? "text-amber-400"
              : "text-slate-500",
        )}
      />
      <div className="min-w-0 leading-none">
        <div
          className={cn(
            "whitespace-nowrap font-mono text-[15px] font-semibold tabular-nums tracking-wide",
            display.tone === "ready"
              ? "text-emerald-300"
              : display.tone === "warning"
                ? "text-amber-300"
                : "text-slate-300",
          )}
        >
          {display.value}
        </div>
        <div className="mt-1 max-w-[116px] truncate text-[9px] font-medium uppercase tracking-wide text-slate-400">
          {display.label}
        </div>
      </div>
    </div>
  );
}
