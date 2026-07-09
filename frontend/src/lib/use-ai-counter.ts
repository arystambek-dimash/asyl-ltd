"use client";
import { useCallback, useEffect, useState } from "react";
import { api, apiError } from "@/lib/api";

/**
 * AI-подсчёт мешков на камере (ai_service через бэкенд-прокси).
 *
 * Состояние живёт на самом AI-сервисе и только опрашивается: перезагрузка
 * страницы или второй планшет видят ту же картину. Пока модель работает —
 * поллинг раз в 1.5 с (живой счётчик), иначе редкий (подхватить чужой запуск).
 */
export interface AiStatus {
  running: boolean;
  /** Имя аннотированного потока в go2rtc/MediaMTX (cam2ai). */
  stream?: string;
  /** "запуск..." | "онлайн" | "переподключение: ..." */
  status?: string;
  fps?: number;
  total?: number;
  weight?: number;
  per_color?: Record<string, number>;
}

const POLL_LIVE_MS = 1500;
const POLL_IDLE_MS = 10_000;

/** cam — путь камеры у ai_service/MediaMTX: cam2 или cam_8c26 (по MAC). */
export function useAiCounter(cam: string | null, active: boolean) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!cam) return;
    try {
      const res = await api.get<AiStatus>(`/cameras/${cam}/ai/`);
      setStatus(res.data);
    } catch {
      // тик статуса не должен ронять пост — не настроен/недоступен ≈ выключен
      setStatus(null);
    }
  }, [cam]);

  // Смена камеры или уход с шага — прежний статус больше не про эту камеру.
  useEffect(() => {
    setStatus(null);
    setError("");
  }, [cam, active]);

  const running = !!status?.running;
  useEffect(() => {
    if (!active || !cam) return;
    refresh();
    const t = setInterval(() => {
      if (!document.hidden) refresh();
    }, running ? POLL_LIVE_MS : POLL_IDLE_MS);
    return () => clearInterval(t);
  }, [active, cam, refresh, running]);

  const act = useCallback(async (fn: () => Promise<{ data: AiStatus }>) => {
    setBusy(true);
    setError("");
    try {
      const res = await fn();
      setStatus(res.data);
    } catch (e) {
      setError(apiError(e));
      throw e; // вызывающий решает, важна ли ошибка (стоп при завершении — нет)
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(
    () => act(() => api.post<AiStatus>(`/cameras/${cam}/ai/`, {})),
    [act, cam],
  );
  const stop = useCallback(
    () => act(() => api.delete<AiStatus>(`/cameras/${cam}/ai/`)),
    [act, cam],
  );
  const reset = useCallback(
    () => act(() => api.post<AiStatus>(`/cameras/${cam}/ai/reset/`, {})),
    [act, cam],
  );

  return { status, running, busy, error, start, stop, reset };
}

export type AiCounter = ReturnType<typeof useAiCounter>;
