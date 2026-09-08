import type { CameraFeed } from "@/components/camera-wall";
import type { AiCountingSession, Order } from "@/lib/types";

/** Камера с потоком (locked-камеры не играют и AI не считают). */
export type PlayableCamera = CameraFeed & { src: string };

// Цвет партии из ai_service (Blue_50, White…) → точка-индикатор в чипе.
export const BAG_COLORS: [RegExp, string][] = [
  [/blue/i, "#3b82f6"],
  [/green/i, "#22c55e"],
  [/red/i, "#ef4444"],
  [/yellow/i, "#eab308"],
  [/orange/i, "#f97316"],
  [/black/i, "#27272a"],
  [/white/i, "#e4e4e7"],
];

export function bagColor(name: string): string {
  return BAG_COLORS.find(([re]) => re.test(name))?.[1] ?? "var(--muted-foreground)";
}

// Новый Windows AI-сервис возвращает machine-readable `online`, а старый
// сервис возвращал локализованное `онлайн`. Во время плавного обновления
// production принимаем оба контракта, чтобы готовый процессор не выглядел как
// бесконечно прогревающийся.
export function isAiOnlineStatus(status?: string): boolean {
  const normalized = status?.trim().toLowerCase();
  return normalized === "online" || normalized === "онлайн";
}

/** Первая запись по ключу: при дублях (несколько сессий заказа) берётся ранняя. */
export function indexFirstBy<T, K>(items: readonly T[], keyOf: (item: T) => K | null | undefined): Map<K, T> {
  const index = new Map<K, T>();
  for (const item of items) {
    const key = keyOf(item);
    if (key != null && !index.has(key)) index.set(key, item);
  }
  return index;
}

/**
 * Камера → заказ, который её занимает: loading_camera активных заказов, а
 * поверх — живые сессии (они точнее, чем поле заказа).
 */
export function cameraOwnersFor(
  orders: readonly Order[] | null | undefined,
  sessions: readonly AiCountingSession[] | null | undefined,
): Record<string, number> {
  const result: Record<string, number> = {};
  for (const order of orders ?? []) {
    if (order.loading_camera && ["confirmed", "arrived", "loading"].includes(order.status)) {
      result[order.loading_camera] ??= order.id;
    }
  }
  for (const session of sessions ?? []) result[session.camera] = session.order_id;
  return result;
}
