"use client";
import { useState } from "react";
import type { ClientDebt } from "@/lib/types";
import { matchesDebtQuery } from "./debt-state";
import { useOverdueCheck } from "./use-overdue-check";

const PAGE = 25;

/**
 * Список должников — общий для десктопной таблицы и мобильного списка:
 * локальный поиск, «Проверить просрочки» и ленивый показ по 25 строк
 * (данные уже загружены целиком, простыню не разворачиваем).
 */
export function useDebtList(rows: ClientDebt[], reload: () => void) {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const overdue = useOverdueCheck(reload);
  const filtered = rows.filter((row) => matchesDebtQuery(row, query));
  const visible = filtered.slice(0, limit);
  return {
    query,
    setQuery,
    overdue,
    filtered,
    visible,
    /** Пропсы для `<LoadMore>`. */
    more: {
      shown: visible.length,
      total: filtered.length,
      hasMore: filtered.length > visible.length,
      onClick: () => setLimit((current) => current + PAGE),
    },
  };
}
