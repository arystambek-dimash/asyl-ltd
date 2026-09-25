import { vi } from "vitest";
import type { usePagedApi } from "@/lib/use-paged-api";

/** Состояние useApi для мока: данные загружены; загрузку, ошибку и reload задаёт тест. */
export function apiState<T>(
  data: T | null,
  {
    loading = false,
    error = "",
    reload = vi.fn(async () => undefined),
  }: { loading?: boolean; error?: string; reload?: () => unknown } = {},
) {
  return { data, loading, error, errorStatus: null, reload, setData: vi.fn() };
}

/** Состояние usePagedApi для мока: одна загруженная страница `items`; колбэки и флаги задаёт тест. */
export function pagedState<T>(items: T[], fields: Partial<ReturnType<typeof usePagedApi<T>>> = {}) {
  return {
    items,
    count: items.length,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    refreshError: "",
    reload: vi.fn(async () => undefined),
    refresh: vi.fn(async () => undefined),
    loadMore: vi.fn(),
    applyItems: vi.fn(),
    ...fields,
  };
}
