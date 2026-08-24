"use client";

import { RefreshCw, Scale } from "lucide-react";
import type { TruckScalePreview } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const TONNES_FORMATTER = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

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
    return { value: weight, label: data.capturable ? "Снимок стабилен" : "Снимок готов", tone: "ready" };
  }
  if (data.state === "unstable" && weight) {
    return { value: `≈ ${weight}`, label: "Снимок меняется", tone: "warning" };
  }

  const labels: Record<TruckScalePreview["state"], string> = {
    ready: "Некорректный вес",
    unstable: "Снимок меняется",
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

export function LiveScaleStatus({
  active,
  scaleKey,
  label,
}: {
  active: boolean;
  scaleKey: "wagon" | "truck";
  label: string;
}) {
  const { data, loading, error, reload } = useApi<TruckScalePreview>(
    active ? `/truck-scales/${scaleKey}/reading/` : null,
  );

  if (!active) return null;

  const display = displayState(data, loading, error);
  const accessibleLabel = `Весы «${label}»: ${display.value}, ${display.label}`;

  return (
    <div
      role="group"
      aria-label={accessibleLabel}
      title={`${label}: ${display.label}`}
      className="flex h-9 min-w-[140px] shrink-0 items-center gap-2 rounded-md border border-[#26382a] bg-[#101511] px-2.5 shadow-inner xl:min-w-[164px]"
    >
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        Весы «{label}»: {display.value}, {display.label}
      </span>
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
        <div className="mt-1 max-w-[82px] truncate text-[9px] font-medium uppercase tracking-wide text-slate-400 xl:max-w-[110px]">
          {label} · {display.label}
        </div>
      </div>
      <button
        type="button"
        aria-label={`Обновить весы «${label}»`}
        title={`Обновить весы «${label}»`}
        disabled={loading}
        onClick={() => void reload()}
        className="ml-auto flex size-6 shrink-0 items-center justify-center rounded text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 disabled:cursor-wait disabled:opacity-50"
      >
        <RefreshCw aria-hidden="true" className={cn("size-3.5", loading && "animate-spin")} />
      </button>
    </div>
  );
}
