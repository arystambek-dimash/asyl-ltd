"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AxiosError } from "axios";
import { api, apiError, isCanceledRequest } from "@/lib/api";

export function useApi<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const latestRequest = useRef(0);
  const activeUrl = useRef<string | null>(null);

  /** Записать authoritative-состояние (обычно ответ собственного PUT/POST).
   *
   * Запрос, начатый ДО записи, может завершиться после неё и вернуть прежнее
   * состояние — на экране изменение «слетало» бы. Поэтому такая запись
   * отменяет ответы в полёте: свежий результат мутации важнее их.
   */
  const commit = useCallback((next: T | null) => {
    activeController.current?.abort();
    activeController.current = null;
    latestRequest.current += 1;
    setData(next);
    setError("");
    setErrorStatus(null);
    setLoading(false);
  }, []);

  const reload = useCallback(async () => {
    if (!url) {
      activeController.current?.abort();
      activeController.current = null;
      latestRequest.current += 1;
      setData(null);
      setError("");
      setErrorStatus(null);
      setLoading(false);
      return;
    }
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    const requestId = ++latestRequest.current;
    setLoading(true);
    try {
      const res = await api.get<T>(url, { signal: controller.signal });
      if (requestId !== latestRequest.current) return;
      setData(res.data);
      setError("");
      setErrorStatus(null);
    } catch (e) {
      if (requestId !== latestRequest.current || isCanceledRequest(e)) return;
      setError(apiError(e));
      setErrorStatus((e as AxiosError).response?.status ?? null);
    } finally {
      if (requestId === latestRequest.current) {
        setLoading(false);
        if (activeController.current === controller) activeController.current = null;
      }
    }
  }, [url]);

  useEffect(() => {
    if (activeUrl.current !== url) {
      activeUrl.current = url;
      setData(null);
      setError("");
      setErrorStatus(null);
    }
    void reload();
    return () => {
      latestRequest.current += 1;
      activeController.current?.abort();
      activeController.current = null;
    };
  }, [reload, url]);
  const isCurrentUrl = activeUrl.current === url;
  return {
    data: isCurrentUrl ? data : null,
    loading: url ? !isCurrentUrl || loading : false,
    error: isCurrentUrl ? error : "",
    errorStatus: isCurrentUrl ? errorStatus : null,
    reload,
    setData: commit,
  };
}
