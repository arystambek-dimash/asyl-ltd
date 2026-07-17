"use client";
import { useCallback, useEffect, useRef, useState } from "react";
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
  /** Глобальный GPU-слот занят другим заказом. Видео при этом доступно. */
  busy?: boolean;
  available?: boolean;
  owned_by_order?: boolean;
  session_id?: number;
  session_order_id?: number;
  session_camera?: string;
  session_started_at?: string;
  session_started_by_id?: number | null;
  session_started_by_name?: string;
  can_stop?: boolean;
  code?: string;
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
const POLL_BUSY_MS = 2500;
const POLL_IDLE_MS = 10_000;

/** cam — NVR-путь камеры у ai_service/MediaMTX, строго cam<N>. */
export function useAiCounter(cam: string | null, orderId: number | null, active: boolean) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const latestPoll = useRef(0);

  const refresh = useCallback(async () => {
    if (!cam || !orderId) return;
    // Ответы поллинга могут приходить не по порядку — устаревший тик не должен
    // откатывать счётчик назад.
    const requestId = ++latestPoll.current;
    try {
      const res = await api.get<AiStatus>(`/cameras/${cam}/ai/?order_id=${orderId}`);
      if (requestId === latestPoll.current) setStatus(res.data);
    } catch {
      // тик статуса не должен ронять пост — не настроен/недоступен ≈ выключен
      if (requestId === latestPoll.current) setStatus(null);
    }
  }, [cam, orderId]);

  // Смена камеры или уход с шага — прежний статус больше не про эту камеру.
  useEffect(() => {
    setStatus(null);
    setError("");
  }, [cam, orderId, active]);

  const running = !!status?.running;
  const occupied = !!status?.busy;
  useEffect(() => {
    if (!active || !cam || !orderId) return;
    refresh();
    const t = setInterval(() => {
      if (!document.hidden) refresh();
    }, running ? POLL_LIVE_MS : occupied ? POLL_BUSY_MS : POLL_IDLE_MS);
    return () => clearInterval(t);
  }, [active, cam, orderId, occupied, refresh, running]);

  const act = useCallback(async (fn: () => Promise<{ data: AiStatus }>) => {
    setBusy(true);
    setError("");
    try {
      const res = await fn();
      latestPoll.current += 1; // ответ действия свежее любого выпущенного тика
      setStatus(res.data);
    } catch (e) {
      setError(apiError(e));
      throw e; // вызывающий решает, важна ли ошибка (стоп при завершении — нет)
    } finally {
      setBusy(false);
    }
  }, []);

  // Дублируем order_id в query и JSON. Query переживает старые proxy/body
  // настройки и делает привязку заказа видимой в access-log; JSON оставляем
  // для обратной совместимости API.
  const orderParams = useCallback(() => ({ params: { order_id: orderId } }), [orderId]);

  const start = useCallback(
    () => act(() => api.post<AiStatus>(
      `/cameras/${cam}/ai/`,
      { order_id: orderId },
      orderParams(),
    )),
    [act, cam, orderId, orderParams],
  );
  const stop = useCallback(
    () => act(() => api.delete<AiStatus>(`/cameras/${cam}/ai/`, {
      ...orderParams(),
      data: { order_id: orderId },
    })),
    [act, cam, orderId, orderParams],
  );
  const reset = useCallback(
    () => act(() => api.post<AiStatus>(
      `/cameras/${cam}/ai/reset/`,
      { order_id: orderId },
      orderParams(),
    )),
    [act, cam, orderId, orderParams],
  );

  return { status, running, occupied, busy, error, orderId, start, stop, reset };
}

export type AiCounter = ReturnType<typeof useAiCounter>;
