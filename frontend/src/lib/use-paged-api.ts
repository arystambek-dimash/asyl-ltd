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
 * список заново с первой страницы.
 */
export function usePagedApi<T>(baseUrl: string | null, pageSize = 50) {
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(Boolean(baseUrl));
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const pageRef = useRef(1);
  const latestRequest = useRef(0);
  const activeController = useRef<AbortController | null>(null);

  const fetchPage = useCallback(
    async (page: number, append: boolean) => {
      if (!baseUrl) {
        activeController.current?.abort();
        activeController.current = null;
        latestRequest.current += 1;
        setItems([]);
        setCount(0);
        setHasMore(false);
        setError("");
        setLoading(false);
        setLoadingMore(false);
        return;
      }
      activeController.current?.abort();
      const controller = new AbortController();
      activeController.current = controller;
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
      } catch (e) {
        if (requestId !== latestRequest.current || isCanceledRequest(e)) return;
        setError(apiError(e));
      } finally {
        if (requestId === latestRequest.current) {
          setLoading(false);
          setLoadingMore(false);
          if (activeController.current === controller) activeController.current = null;
        }
      }
    },
    [baseUrl, pageSize],
  );

  const reload = useCallback(() => fetchPage(1, false), [fetchPage]);

  const loadMore = useCallback(() => {
    if (!hasMore || loadingMore) return;
    void fetchPage(pageRef.current + 1, true);
  }, [fetchPage, hasMore, loadingMore]);

  useEffect(() => {
    pageRef.current = 1;
    setItems([]);
    setCount(0);
    setHasMore(false);
    void reload();
    return () => {
      latestRequest.current += 1;
      activeController.current?.abort();
      activeController.current = null;
    };
  }, [reload]);

  return { items, count, hasMore, loading, loadingMore, error, reload, loadMore };
}
