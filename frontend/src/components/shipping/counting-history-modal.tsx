"use client";

import { useEffect, useMemo, useState } from "react";
import { Cctv, Film } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";
import { resolveApiMediaUrl } from "@/lib/safe-url";
import type { AiCountingHistory, AiRecording } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";

/** «Как камера считала мешки» — итог модели и запись с компьютера камер. */
export function CountingHistoryModal({ history, onClose }: { history: AiCountingHistory | null; onClose: () => void }) {
  const recordingUrl = history ? `/cameras/ai/history/${history.id}/recording/` : null;
  const { data: recording, loading, error } = useApi<AiRecording>(recordingUrl);
  const [segmentIndex, setSegmentIndex] = useState(0);
  useEffect(() => setSegmentIndex(0), [history?.id]);
  const segments = recording?.segments ?? [];
  const segment = segments[segmentIndex] ?? segments[0];
  const videoUrl = useMemo(() => {
    if (!segment?.video_url || typeof window === "undefined") return "";
    return resolveApiMediaUrl(segment.video_url, String(api.defaults.baseURL || ""), window.location.origin);
  }, [segment?.video_url]);
  const total = history?.final_total ?? history?.last_status?.total;

  return (
    <Modal
      open={!!history}
      onClose={onClose}
      mobileFullscreen
      className="max-w-3xl"
      eyebrow={history ? `История отгрузки · заказ #${history.order_id}` : undefined}
      title="Как камера считала мешки"
      description="Запись хранится на компьютере камер и не занимает место на сервере."
    >
      {history && (
        <div className="grid gap-4 md:grid-cols-[0.85fr_1.4fr]">
          <div className="flex flex-col gap-3">
            <div className="rounded-2xl bg-slate-950 p-5 text-white">
              <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-cyan-300">Итог модели</div>
              <div className="mt-2 text-6xl font-black tabular-nums tracking-[-0.06em]">{total ?? "—"}</div>
              <div className="text-sm text-white/55">мешков насчитано камерой</div>
            </div>
            <div className="rounded-2xl border p-4 text-sm">
              <div className="flex items-center gap-2 font-bold">
                <Cctv className="size-4 text-blue-600" /> {history.camera_name}
              </div>
              <div className="mt-3 grid gap-2 text-xs text-slate-500">
                <span>
                  <b className="text-slate-700">Клиент:</b> {history.order_client_name}
                </span>
                <span>
                  <b className="text-slate-700">Оператор:</b> {history.started_by_name || "—"}
                </span>
                <span>
                  <b className="text-slate-700">Начало:</b> {formatDateTime(history.started_at)}
                </span>
                {history.ended_at && (
                  <span>
                    <b className="text-slate-700">Завершение:</b> {formatDateTime(history.ended_at)}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="min-h-[280px] overflow-hidden rounded-2xl border bg-[#111315]">
            {videoUrl ? (
              <div className="flex h-full flex-col">
                <video
                  key={videoUrl}
                  controls
                  preload="metadata"
                  src={videoUrl}
                  className="aspect-video w-full flex-1 bg-black object-contain"
                />
                {segments.length > 1 && (
                  <div className="flex gap-2 overflow-x-auto border-t border-white/10 p-3">
                    {segments.map((item, index) => (
                      <button
                        key={`${item.start}-${index}`}
                        type="button"
                        onClick={() => setSegmentIndex(index)}
                        className={cn(
                          "shrink-0 rounded-lg px-3 py-2 text-xs font-semibold",
                          index === segmentIndex
                            ? "bg-white text-black"
                            : "bg-white/10 text-white/70 hover:bg-white/15",
                        )}
                      >
                        Фрагмент {index + 1} · {Math.max(1, Math.round(item.duration / 60))} мин
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex h-full min-h-[280px] flex-col items-center justify-center px-8 text-center text-white/50">
                <Film className="size-10" />
                <div className="mt-3 text-sm font-bold text-white/75">
                  {loading ? "Ищем запись на компьютере камер…" : "Запись сейчас недоступна"}
                </div>
                <p className="mt-1 max-w-sm text-xs leading-relaxed">
                  {error ||
                    recording?.detail ||
                    (history.has_recording
                      ? "Компьютер камер выключен либо срок хранения записи истёк."
                      : "Эта сессия была завершена до включения локального архива.")}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
