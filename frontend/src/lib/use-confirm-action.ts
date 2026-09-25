"use client";

import { useState } from "react";

import { apiError } from "@/lib/api";

export interface ConfirmAction<T> {
  /** Запись, для которой открыто окно подтверждения; null — окно закрыто. */
  item: T | null;
  open: (item: T) => void;
  close: () => void;
  busy: boolean;
  error: string;
  confirm: () => Promise<void>;
  /** Состояние окна для ConfirmDialog: `<ConfirmDialog {...action.dialog} title=… />`. */
  dialog: {
    open: boolean;
    onClose: () => void;
    busy: boolean;
    error: string;
    onConfirm: () => void;
  };
}

/**
 * «Удалить с подтверждением»: выбранная запись, занятость и ошибка окна.
 * run — само действие вместе с обновлением списков. После успеха окно
 * закрывается, при ошибке остаётся открытым и показывает причину.
 */
export function useConfirmAction<T>(run: (item: T) => Promise<unknown>): ConfirmAction<T> {
  const [item, setItem] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function open(next: T) {
    setError("");
    setItem(next);
  }

  // Пока запрос идёт, окно не закрываем: иначе ошибка придёт в закрытое окно.
  function close() {
    if (!busy) setItem(null);
  }

  async function confirm() {
    if (item === null || busy) return;
    setBusy(true);
    setError("");
    try {
      await run(item);
      setItem(null);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return {
    item,
    open,
    close,
    busy,
    error,
    confirm,
    dialog: { open: item !== null, onClose: close, busy, error, onConfirm: () => void confirm() },
  };
}
