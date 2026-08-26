"use client";

import { useState } from "react";
import { Camera, ScanLine, Truck, VideoOff } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import { cn } from "@/lib/utils";

const VEHICLE_PLATE_CAMERA = "cam1";
const VEHICLE_PLATE_OCR_SOURCE = "main";

/**
 * Live view of the fixed vehicle-plate lane used by automatic grain export.
 * CameraStream keeps the go2rtc signalling behind the authenticated endpoint;
 * this component only selects the logical stream name.
 */
export function VehiclePlateCameraWorkspace() {
  const [streamOnline, setStreamOnline] = useState(false);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-sky-700">Камера автоматического вывоза</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Камера проходной показывает машину, номер которой используется для автоматического рейса.
        </p>
      </div>

      <section
        aria-label="Камера проходной на вывоз"
        className="overflow-hidden rounded-[28px] border border-slate-800 bg-[#111318] text-white shadow-[0_24px_60px_rgba(15,23,42,0.18)]"
      >
        <div className="grid lg:grid-cols-[1.55fr_0.85fr]">
          <div className="relative aspect-video min-h-72 overflow-hidden bg-black">
            <CameraStream
              src={VEHICLE_PLATE_CAMERA}
              onStateChange={setStreamOnline}
              className="absolute inset-0 size-full object-cover"
            />
            {!streamOnline && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black text-white/40">
                <VideoOff className="size-7" />
                <span className="text-xs">Ожидаем видеопоток</span>
              </div>
            )}

            <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/80 to-transparent p-4 pb-12">
              <span className="flex items-center gap-2 rounded-full border border-sky-300/25 bg-black/45 px-3 py-1.5 text-[10px] font-bold tracking-[0.14em] text-sky-200 backdrop-blur-md">
                <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
                ПРОХОДНАЯ · ВЫВОЗ
              </span>
              <span className="rounded-full bg-black/45 px-3 py-1.5 text-[10px] font-semibold text-white/65 backdrop-blur-md">
                Камера {VEHICLE_PLATE_CAMERA} · OCR: {VEHICLE_PLATE_OCR_SOURCE}
              </span>
            </div>

            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-4 pt-14">
              <p className="text-lg font-bold">Камера проходной</p>
              <p className="mt-0.5 text-xs text-white/50">Распознавание номера машины</p>
            </div>
          </div>

          <div className="flex flex-col p-6">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-sky-300">Прямой эфир</p>
                <h2 className="mt-2 text-2xl font-bold tracking-tight">Автоматический вывоз</h2>
              </div>
              <span
                className={cn(
                  "rounded-full border px-3 py-1 text-[10px] font-bold",
                  streamOnline
                    ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-300"
                    : "border-amber-400/25 bg-amber-400/10 text-amber-200",
                )}
              >
                {streamOnline ? "В ЭФИРЕ" : "НЕТ СИГНАЛА"}
              </span>
            </div>

            <div className="mt-7 grid gap-3">
              <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                <span className="flex size-10 items-center justify-center rounded-xl bg-sky-400/10 text-sky-300">
                  <Camera className="size-5" />
                </span>
                <div>
                  <p className="text-[11px] text-white/40">Источник распознавания</p>
                  <p className="mt-0.5 text-sm font-bold">
                    Камера {VEHICLE_PLATE_CAMERA} · OCR: {VEHICLE_PLATE_OCR_SOURCE}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                <span className="flex size-10 items-center justify-center rounded-xl bg-emerald-400/10 text-emerald-300">
                  <ScanLine className="size-5" />
                </span>
                <div>
                  <p className="text-[11px] text-white/40">Что фиксирует</p>
                  <p className="mt-0.5 text-sm font-bold">Номер машины на проходной</p>
                </div>
              </div>
            </div>

            <div className="mt-auto pt-6">
              <div className="flex items-start gap-3 rounded-2xl border border-sky-300/15 bg-sky-300/[0.06] p-4 text-xs leading-5 text-sky-50/65">
                <Truck className="mt-0.5 size-4 shrink-0 text-sky-300" />
                <p>Камера помогает оператору наглядно контролировать автоматическое создание рейса на вывоз.</p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
