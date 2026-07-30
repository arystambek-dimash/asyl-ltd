"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiError, isCanceledRequest } from "@/lib/api";

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
  /** "запуск..." | "online" (legacy: "онлайн") | "переподключение: ..." */
  status?: string;
  fps?: number;
  total?: number;
  weight?: number;
  per_color?: Record<string, number>;
}

const POLL_LIVE_MS = 1500;
const POLL_BUSY_MS = 2500;
const POLL_IDLE_MS = 10_000;

function pollDelay(status: AiStatus | null): number {
  return status?.running ? POLL_LIVE_MS : status?.busy ? POLL_BUSY_MS : POLL_IDLE_MS;
}

/** cam — NVR-путь камеры у ai_service/MediaMTX, строго cam<N>. */
export function useAiCounter(cam: string | null, orderId: number | null, active: boolean) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [commandError, setCommandError] = useState("");
  const [pollError, setPollError] = useState("");
  const [stale, setStale] = useState(false);
  const latestPoll = useRef(0);
  const latestCommand = useRef(0);
  const scopeGeneration = useRef(0);
  const statusRef = useRef<AiStatus | null>(null);
  const reschedulePolling = useRef<() => void>(() => {});
  const commandQueue = useRef<Promise<void>>(Promise.resolve());
  const actionScope = useMemo(() => ({ active, cam, orderId }), [active, cam, orderId]);
  const currentActionScope = useRef(actionScope);
  currentActionScope.current = actionScope;

  // Polls are serialized and scheduled only after the previous request has
  // settled. Scope changes abort and invalidate any response from the old
  // camera/order instead of letting it restore stale status.
  useEffect(() => {
    const scope = ++scopeGeneration.current;
    latestPoll.current += 1;
    latestCommand.current += 1;
    commandQueue.current = Promise.resolve();
    statusRef.current = null;
    setStatus(null);
    setCommandError("");
    setPollError("");
    setStale(false);
    setBusy(false);
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
    reschedulePolling.current = () => schedule(pollDelay(statusRef.current));
    document.addEventListener("visibilitychange", pollNow);
    window.addEventListener("online", pollNow);
    void poll();

    return () => {
      disposed = true;
      scopeGeneration.current += 1;
      latestPoll.current += 1;
      latestCommand.current += 1;
      reschedulePolling.current = () => {};
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", pollNow);
      window.removeEventListener("online", pollNow);
    };
  }, [active, cam, orderId]);

  const running = !!status?.running;
  const occupied = !!status?.busy;

  const act = useCallback(
    (fn: () => Promise<{ data: AiStatus }>): Promise<void> => {
      // A callback retained by a previous render must not issue a command for
      // its old camera/order after the operator changes the active post.
      if (
        actionScope !== currentActionScope.current ||
        !actionScope.active ||
        !actionScope.cam ||
        !actionScope.orderId
      ) {
        return Promise.resolve();
      }

      const scope = scopeGeneration.current;
      const command = ++latestCommand.current;
      latestPoll.current += 1;
      setBusy(true);
      setCommandError("");
      setPollError("");

      const run = async () => {
        if (actionScope !== currentActionScope.current || scope !== scopeGeneration.current) return;
        try {
          const res = await fn();
          if (
            actionScope !== currentActionScope.current ||
            scope !== scopeGeneration.current ||
            command !== latestCommand.current
          ) {
            return;
          }
          latestPoll.current += 1; // ответ действия свежее любого выпущенного тика
          statusRef.current = res.data;
          setStatus(res.data);
          setPollError("");
          setStale(false);
          reschedulePolling.current();
        } catch (cause) {
          if (
            actionScope === currentActionScope.current &&
            scope === scopeGeneration.current &&
            command === latestCommand.current
          ) {
            setCommandError(apiError(cause));
          }
          throw cause; // вызывающий решает, важна ли ошибка (стоп при завершении — нет)
        } finally {
          if (
            actionScope === currentActionScope.current &&
            scope === scopeGeneration.current &&
            command === latestCommand.current
          ) {
            setBusy(false);
          }
        }
      };

      const result = commandQueue.current.then(run);
      // A failed command must reject for its own caller but must not poison the
      // queue: a later stop/reset still has to run in invocation order.
      commandQueue.current = result.catch(() => undefined);
      return result;
    },
    [actionScope],
  );

  // Дублируем order_id в query и JSON. Query переживает старые proxy/body
  // настройки и делает привязку заказа видимой в access-log; JSON оставляем
  // для обратной совместимости API.
  const orderParams = useCallback(() => ({ params: { order_id: orderId } }), [orderId]);

  const start = useCallback(
    () => act(() => api.post<AiStatus>(`/cameras/${cam}/ai/`, { order_id: orderId }, orderParams())),
    [act, cam, orderId, orderParams],
  );
  const stop = useCallback(
    (completeOrder = false) =>
      act(() =>
        api.delete<AiStatus>(`/cameras/${cam}/ai/`, {
          params: { order_id: orderId, complete_order: completeOrder ? 1 : 0 },
          data: { order_id: orderId, complete_order: completeOrder },
        }),
      ),
    [act, cam, orderId],
  );
  const reset = useCallback(
    () => act(() => api.post<AiStatus>(`/cameras/${cam}/ai/reset/`, { order_id: orderId }, orderParams())),
    [act, cam, orderId, orderParams],
  );

  return {
    status,
    running,
    occupied,
    busy,
    stale,
    error: commandError || pollError,
    orderId,
    start,
    stop,
    reset,
  };
}

export type AiCounter = ReturnType<typeof useAiCounter>;
