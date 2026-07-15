"use client";

/** Лёгкий стор всплывающих алертов. showToast() можно звать из любого кода
 * (в т.ч. из интерцепторов api.ts), <Toaster /> в корневом layout подписывается и рисует. */

export interface Toast {
  id: number;
  message: string;
}

type Listener = (toasts: Toast[]) => void;

const TTL_MS = 5000;

let toasts: Toast[] = [];
let listeners: Listener[] = [];
let nextId = 1;

function emit() {
  listeners.forEach((l) => l(toasts));
}

export function showToast(message: string) {
  // Одинаковые сообщения от параллельных запросов не дублируем.
  if (toasts.some((t) => t.message === message)) return;
  const id = nextId++;
  toasts = [...toasts, { id, message }];
  emit();
  setTimeout(() => dismissToast(id), TTL_MS);
}

export function dismissToast(id: number) {
  if (!toasts.some((t) => t.id === id)) return;
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.push(listener);
  listener(toasts);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}
