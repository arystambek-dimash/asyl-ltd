"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiError, isCanceledRequest } from "@/lib/api";

/** Стандартный конверт DRF-пагинации (включается параметром ?page=). */
export interface PagedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/**
 * Ленивая подгрузка списка страницами: первая грузится сразу, остальные —
 * по loadMore(), результаты накапливаются. Смена URL (фильтры) начинает
 * список заново с первой страницы. Для опрашиваемых экранов — тихий
 * refresh() и applyItems() для ответа своей мутации.
 */
export function usePagedApi<T>(baseUrl: string | null, pageSize = 50) {
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(Boolean(baseUrl));
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const itemsRef = useRef<T[]>([]);
  itemsRef.current = items;
  const pageRef = useRef(1);
  const latestRequest = useRef(0);
  const activeController = useRef<AbortController | null>(null);
  // Загрузка страницы в полёте (смена фильтра, «Ещё»); у тихого опроса — null.
  const pendingPage = useRef<{ page: number; append: boolean } | null>(null);

  const fetchPage = useCallback(
    async (page: number, append: boolean) => {
      if (!baseUrl) {
        activeController.current?.abort();
        activeController.current = null;
        pendingPage.current = null;
        latestRequest.current += 1;
        setItems([]);
        setCount(0);
        setHasMore(false);
        setError("");
        setRefreshError("");
        setLoading(false);
        setLoadingMore(false);
        return;
      }
      activeController.current?.abort();
      const controller = new AbortController();
      activeController.current = controller;
      pendingPage.current = { page, append };
      const requestId = ++latestRequest.current;
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const separator = baseUrl.includes("?") ? "&" : "?";
        const url = `${baseUrl}${separator}page=${page}&page_size=${pageSize}`;
        const res = await api.get<PagedResponse<T>>(url, { signal: controller.signal });
        if (requestId !== latestRequest.current) return;
        pageRef.current = page;
        setCount(res.data.count);
        setHasMore(Boolean(res.data.next));
        setItems((current) => (append ? [...current, ...res.data.results] : res.data.results));
        setError("");
        setRefreshError("");
      } catch (e) {
        if (requestId !== latestRequest.current || isCanceledRequest(e)) return;
        setError(apiError(e));
      } finally {
        if (requestId === latestRequest.current) {
          setLoading(false);
          setLoadingMore(false);
          if (activeController.current === controller) {
            activeController.current = null;
            pendingPage.current = null;
          }
        }
      }
    },
    [baseUrl, pageSize],
  );

  const reload = useCallback(() => fetchPage(1, false), [fetchPage]);

  /**
   * Тихое обновление для опроса: перечитывает показанные страницы без
   * индикатора загрузки и заменяет список одним разом. Ошибка не трогает ни
   * список, ни `error` — она уходит в `refreshError`, последние данные остаются.
   * Идущая загрузка (фильтр, «Ещё») и так принесёт свежее — тогда пропускаем.
   */
  const refresh = useCallback(async () => {
    if (!baseUrl || activeController.current) return;
    const controller = new AbortController();
    activeController.current = controller;
    const requestId = ++latestRequest.current;
    const separator = baseUrl.includes("?") ? "&" : "?";
    try {
      const pages: PagedResponse<T>[] = [];
      for (let page = 1; page <= pageRef.current; page += 1) {
        const res = await api.get<PagedResponse<T>>(`${baseUrl}${separator}page=${page}&page_size=${pageSize}`, {
          signal: controller.signal,
        });
        if (requestId !== latestRequest.current) return;
        pages.push(res.data);
        if (!res.data.next) break;
      }
      const last = pages[pages.length - 1];
      pageRef.current = pages.length;
      setCount(last.count);
      setHasMore(Boolean(last.next));
      setItems(pages.flatMap((page) => page.results));
      setError("");
      setRefreshError("");
    } catch (e) {
      if (requestId !== latestRequest.current || isCanceledRequest(e)) return;
      setRefreshError(apiError(e));
    } finally {
      if (activeController.current === controller) activeController.current = null;
    }
  }, [baseUrl, pageSize]);

  /**
   * Применить ответ своей мутации к показанному списку (как `setData` у useApi).
   * Запрос в полёте начат до неё и вернул бы прежнее состояние: опрос гасим,
   * а загрузку страницы (новый фильтр, «Ещё») повторяем — иначе список так
   * и остался бы без неё.
   */
  const applyItems = useCallback(
    (update: (items: T[]) => T[]) => {
      const restart = pendingPage.current;
      activeController.current?.abort();
      activeController.current = null;
      pendingPage.current = null;
      latestRequest.current += 1;
      const current = itemsRef.current;
      const next = update(current);
      setItems(next);
      setCount((total) => Math.max(0, total + next.length - current.length));
      if (restart) {
        void fetchPage(restart.page, restart.append);
        return;
      }
      setLoading(false);
      setLoadingMore(false);
    },
    [fetchPage],
  );

  const loadMore = useCallback(() => {
    if (!hasMore || loading || loadingMore) return;
    void fetchPage(pageRef.current + 1, true);
  }, [fetchPage, hasMore, loading, loadingMore]);

  useEffect(() => {
    pageRef.current = 1;
    setItems([]);
    setCount(0);
    setHasMore(false);
    setRefreshError("");
    void reload();
    return () => {
      latestRequest.current += 1;
      activeController.current?.abort();
      activeController.current = null;
      pendingPage.current = null;
    };
  }, [reload]);

  return { items, count, hasMore, loading, loadingMore, error, refreshError, reload, refresh, loadMore, applyItems };
}
