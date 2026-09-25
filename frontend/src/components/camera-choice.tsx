"use client";

import { useState } from "react";
import { Check, LockKeyhole, VideoOff, type LucideIcon } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import type { PlayableCamera } from "@/lib/shipping-cameras";
import { cn } from "@/lib/utils";

const ACCENTS = {
  blue: {
    card: "border-blue-400 bg-blue-50 shadow-[0_10px_28px_rgba(59,104,210,0.15)] ring-2 ring-blue-500/20",
    check: "border-blue-300 bg-blue-600 text-white",
    icon: "bg-blue-600 text-white",
  },
  amber: {
    card: "border-amber-400 bg-amber-50 shadow-[0_10px_28px_rgba(180,116,24,0.16)] ring-2 ring-amber-500/20",
    check: "border-amber-300 bg-amber-500 text-white",
    icon: "bg-amber-500 text-white",
  },
} as const;

/** Карточка выбора камеры в окнах назначения: живой кадр, статус сигнала и отметка выбора. */
export function CameraChoice({
  camera,
  checked,
  onToggle,
  accent,
  icon: Icon,
  disabled = false,
  disabledReason,
}: {
  camera: PlayableCamera;
  checked: boolean;
  onToggle: () => void;
  accent: keyof typeof ACCENTS;
  icon: LucideIcon;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [streamOnline, setStreamOnline] = useState(false);
  const colors = ACCENTS[accent];

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      disabled={disabled}
      aria-label={disabledReason ? `${camera.zone}: ${disabledReason}` : undefined}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? colors.card
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
        disabled &&
          "cursor-not-allowed border-amber-200 bg-amber-50/60 opacity-75 hover:translate-y-0 hover:shadow-none",
      )}
    >
      <div className="relative aspect-video overflow-hidden bg-[#151821]">
        <CameraStream
          src={camera.src}
          onStateChange={setStreamOnline}
          className="absolute inset-0 size-full object-cover transition duration-300 group-hover:scale-[1.02]"
        />

        {!streamOnline && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-slate-950/75 text-white/45">
            <VideoOff className="size-5" />
            <span className="text-[11px]">Нет изображения</span>
          </div>
        )}

        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/65 to-transparent px-3 pb-8 pt-2.5">
          <span className="flex items-center gap-1.5 rounded-full bg-black/35 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
            {streamOnline ? "ОНЛАЙН" : "НЕТ СИГНАЛА"}
          </span>
          <span
            className={cn(
              "flex size-7 items-center justify-center rounded-full border backdrop-blur-md transition",
              checked ? colors.check : "border-white/35 bg-black/25 text-transparent",
            )}
          >
            <Check className="size-4" />
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3 px-3.5 py-3">
        <span
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-xl",
            checked ? colors.icon : "bg-slate-100 text-slate-400",
          )}
        >
          <Icon className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
          <span className="mt-0.5 block truncate text-[11px] text-slate-400">{camera.name}</span>
          {disabledReason && (
            <span className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-amber-700">
              <LockKeyhole className="size-3" /> {disabledReason}
            </span>
          )}
        </span>
      </div>
    </button>
  );
}
