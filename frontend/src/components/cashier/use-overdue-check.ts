"use client";
import { useState } from "react";
import { api, apiError } from "@/lib/api";

/** «Проверить просрочки» — общий для десктопной таблицы и мобильного списка долгов. */
export function useOverdueCheck(reload: () => void) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function run() {
    setBusy(true);
    setMessage("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setMessage(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      reload();
    } catch (e) {
      setMessage(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return { busy, message, run };
}
