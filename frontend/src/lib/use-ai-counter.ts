"use client";
import { useEffect, useRef, useState } from "react";
import { api, apiError, isCanceledRequest } from "@/lib/api";
import type { LineDirection, NormalizedLine } from "@/lib/camera-counting-line";
import type { AlwaysOnDetection } from "@/lib/types";

/**
 * AI-подсчёт мешков на камере (ai_service через бэкенд-прокси).
 *
 * Состояние живёт на самом AI-сервисе и только опрашивается: перезагрузка
 * страницы или второй планшет видят ту же картину. Пока модель работает —
 * поллинг раз в 0.5 с (живой счётчик и рамки), иначе редкий (подхватить чужой запуск).
 */
export interface AiStatus {
  running: boolean;
  /** Глобальный GPU-слот занят другим заказом. Видео при этом доступно. */
  busy?: boolean;
  available?: boolean;
  owned_by_order?: boolean;
  session_id?: number | string | null;
  session_order_id?: number;
  session_camera?: string;
  session_started_at?: string;
  session_started_by_id?: number | null;
  session_started_by_name?: string;
  can_stop?: boolean;
  code?: string;
  /** Имя аннотированного потока в go2rtc/MediaMTX (cam2ai). */
  stream?: string;
  /** Последние рамки модели — рисуем поверх базового camN без camNai. */
  detections?: AlwaysOnDetection[];
  detection_frame?: { width?: number; height?: number } | null;
  /** Фактически применённая процессором линия подсчёта. */
  line?: string | NormalizedLine | null;
  direction?: LineDirection;
  last_frame_at?: string | null;
  /** "запуск..." | "online" (legacy: "онлайн") | "переподключение: ..." */
  status?: string;
  fps?: number;
  total?: number;
  weight?: number;
  per_color?: Record<string, number>;
}

// Активная карточка рисует рамки поверх базового потока. Полторы секунды
// заставляли их заметно отставать от движущегося мешка; два лёгких status
// запроса в секунду сохраняют и счётчик, и оверлей визуально живыми.
const POLL_LIVE_MS = 500;
const POLL_BUSY_MS = 2500;
const POLL_IDLE_MS = 10_000;

function pollDelay(status: AiStatus | null): number {
  return status?.running ? POLL_LIVE_MS : status?.busy ? POLL_BUSY_MS : POLL_IDLE_MS;
}

/** cam — NVR-путь камеры у ai_service/MediaMTX, строго cam<N>. */
export function useAiCounter(cam: string | null, orderId: number | null, active: boolean) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [pollError, setPollError] = useState("");
  const [stale, setStale] = useState(false);
  const latestPoll = useRef(0);
  const scopeGeneration = useRef(0);
  const statusRef = useRef<AiStatus | null>(null);
  // Polls are serialized and scheduled only after the previous request has
  // settled. Scope changes abort and invalidate any response from the old
  // camera/order instead of letting it restore stale status.
  useEffect(() => {
    const scope = ++scopeGeneration.current;
    latestPoll.current += 1;
    statusRef.current = null;
    setStatus(null);
    setPollError("");
    setStale(false);
    if (!active || !cam || !orderId) return;

    let disposed = false;
    let polling = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void poll();
      }, delay);
    };

    const poll = async () => {
      if (disposed || polling) return;
      if (document.hidden) {
        schedule(pollDelay(statusRef.current));
        return;
      }
      polling = true;
      controller = new AbortController();
      const requestId = ++latestPoll.current;
      try {
        const response = await api.get<AiStatus>(`/cameras/${cam}/ai/?order_id=${orderId}`, {
          signal: controller.signal,
        });
        if (disposed || scope !== scopeGeneration.current || requestId !== latestPoll.current) return;
        statusRef.current = response.data;
        setStatus(response.data);
        setPollError("");
        setStale(false);
      } catch (cause) {
        if (disposed || isCanceledRequest(cause)) return;
        if (scope === scopeGeneration.current && requestId === latestPoll.current) {
          // A failed tick is not proof that the processor stopped. Keep the
          // last authoritative status (and therefore its live cadence). Only
          // an initial failure with no usable data is surfaced as an error.
          const hasLastGoodStatus = statusRef.current !== null;
          setStale(hasLastGoodStatus);
          setPollError(hasLastGoodStatus ? "" : apiError(cause));
        }
      } finally {
        polling = false;
        controller = null;
        schedule(pollDelay(statusRef.current));
      }
    };

    const pollNow = () => {
      if (document.hidden || disposed) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void poll();
    };
    document.addEventListener("visibilitychange", pollNow);
    window.addEventListener("online", pollNow);
    void poll();

    return () => {
      disposed = true;
      scopeGeneration.current += 1;
      latestPoll.current += 1;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", pollNow);
      window.removeEventListener("online", pollNow);
    };
  }, [active, cam, orderId]);

  const running = !!status?.running;
  const occupied = !!status?.busy;

  return {
    status,
    running,
    occupied,
    stale,
    error: pollError,
    orderId,
  };
}

export type AiCounter = ReturnType<typeof useAiCounter>;
