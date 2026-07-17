"use client";
import { useEffect, useState } from "react";

/** Отдаёт значение с задержкой: ввод в поиск не шлёт запрос на каждую букву. */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
