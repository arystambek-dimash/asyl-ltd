import type { CameraFeed } from "@/components/camera-wall";
import type { AiCountingSession, AlwaysOnProcessorStatus, CameraContinuousReadiness, Order } from "@/lib/types";

/** Камера с потоком (locked-камеры не играют и AI не считают). */
export type PlayableCamera = CameraFeed & { src: string };

/** Всё, что влияет на доступность камеры для запуска AI-подсчёта. */
export interface CameraAvailabilityContext {
  /** Камеры с видимыми сессиями (`/cameras/ai/sessions/`). */
  busyCameras?: readonly string[];
  /** Глобальное состояние процессоров отгрузки, включая сессии чужих отделов. */
  shippingProcessors?: readonly AlwaysOnProcessorStatus[];
  /** Камера → заказ, за которым она закреплена (loading_camera или сессия). */
  cameraOwners?: Record<string, number>;
  cameraReadiness?: Record<string, CameraContinuousReadiness>;
  /** Общий статус непрерывного контура, когда по-камерной готовности нет. */
  continuousReady?: boolean;
}

export function isCameraReady(camera: Pick<CameraFeed, "src">, context: CameraAvailabilityContext): boolean {
  const readiness = camera.src ? context.cameraReadiness?.[camera.src] : undefined;
  return readiness ? readiness.status === "synced" : context.continuousReady !== false;
}

/** Камеры, занятые сессией — видимой или известной только ПК цеха. */
export function occupiedCameras(context: CameraAvailabilityContext): Set<string> {
  const occupied = new Set(context.busyCameras ?? []);
  for (const processor of context.shippingProcessors ?? []) {
    if (processor.mode === "session") occupied.add(processor.cam);
  }
  return occupied;
}

/**
 * Камеры, на которых можно запустить AI-подсчёт для заказа.
 *
 * Готовый непрерывный процессор и подтверждённый online-источник обязательны:
 * поток с известным src ещё не означает живую камеру. Закреплённая за другим
 * заказом камера недоступна, а закреплённая за этим — доступна даже при
 * активной сессии (перезапуск после «Выключить AI»).
 */
export function availableCamerasForOrder(
  order: Pick<Order, "id"> | null | undefined,
  cameras: readonly PlayableCamera[],
  context: CameraAvailabilityContext,
): PlayableCamera[] {
  const occupied = occupiedCameras(context);
  return cameras.filter((camera) => {
    if (!isCameraReady(camera, context)) return false;
    if (!camera.online) return false;
    const ownerId = context.cameraOwners?.[camera.src];
    if (ownerId != null) return ownerId === order?.id;
    return !occupied.has(camera.src);
  });
}

/** Подпись пустого выбора камеры — объясняет, почему список пуст. */
export function cameraPlaceholder(
  cameras: readonly PlayableCamera[],
  available: readonly PlayableCamera[],
  context: CameraAvailabilityContext,
): string {
  if (!cameras.length) return "Камеры не настроены";
  if (!cameras.some((camera) => isCameraReady(camera, context))) return "Камеры отгрузки ещё не готовы";
  if (!cameras.some((camera) => camera.online)) return "Нет камер онлайн";
  if (!available.length) return "Нет свободных камер";
  return "Выберите камеру";
}

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
