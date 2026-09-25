import type { AiCountingSession, CameraFeed, Order } from "@/lib/types";

/** Камера с потоком (locked-камеры не играют и AI не считают). */
export type PlayableCamera = CameraFeed & { src: string };

/** Камеры, у которых есть поток для просмотра (locked не играют). */
export function playableCameras(cams: CameraFeed[] | null | undefined): PlayableCamera[] {
  return (cams ?? []).filter((c): c is PlayableCamera => !!c.src);
}

/** Логическая камера camN: только её закрепляют за контурами и настраивают линию подсчёта. */
export function isLogicalCamera(src: string): boolean {
  return /^cam[1-9]\d*$/.test(src);
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
