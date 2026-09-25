import { useState } from "react";
import { apiError } from "@/lib/api";

/**
 * Каркас окон ручного взвешивания. Пока окно открыто, рейс не опрашивается
 * (onBusyChange), во время запроса окно не закрыть, после успеха оно
 * закрывается и рейс перечитывается.
 */
export function useWeighingDialog(onBusyChange?: (busy: boolean) => void) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setError("");
    setOpen(true);
    onBusyChange?.(true);
  }

  function close() {
    if (busy) return;
    setOpen(false);
    onBusyChange?.(false);
  }

  async function submit(request: () => Promise<unknown>, onDone: () => void) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await request();
      setOpen(false);
      onBusyChange?.(false);
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return { open, busy, setBusy, error, setError, show, close, submit };
}
