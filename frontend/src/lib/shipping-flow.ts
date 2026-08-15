import type { Order } from "@/lib/types";

export type ShippingBoardStageKey = "waiting" | "loading" | "exit" | "done";

export type ShippingCapabilities = {
  canLoad: boolean;
  canTrain: boolean;
  canShip: boolean;
  canRollback: boolean;
};

export type ShippingBoardAction = "rewind_loading" | "finish_loading" | "ship";
type PostRequest = (url: string, body: Record<string, never>) => Promise<unknown>;

/**
 * Возвращает только штатные переходы поста погрузки.
 *
 * `loaded` намеренно остаётся отдельным незавершённым этапом: сотрудник,
 * который завершает AI-подсчёт, не получает право оформить выезд машины.
 */
export function allowedShippingBoardStages(order: Order, capabilities: ShippingCapabilities): ShippingBoardStageKey[] {
  if (order.status === "shipped") return capabilities.canRollback ? ["waiting"] : [];
  if (order.status === "loaded") return capabilities.canShip ? ["done"] : [];
  if (order.status === "confirmed") return [];

  if (order.status !== "arrived" && order.status !== "loading") return [];

  const stages: ShippingBoardStageKey[] = [];
  if (capabilities.canLoad) stages.push("waiting");

  // Старый/аномальный `arrived` допустим для грузовика. Вагон backend
  // завершает только из `loading`, поэтому не показываем заведомо битый ход.
  const canFinishLoading =
    order.transport_type === "train" ? order.status === "loading" && capabilities.canTrain : capabilities.canLoad;
  if (canFinishLoading) stages.push("exit");
  return stages;
}

/**
 * Сопоставляет перемещение с одним бизнес-действием. В частности, переход в
 * `done` существует только для уже загруженного заказа и означает только
 * отдельный POST оформления выезда.
 */
export function shippingBoardAction(order: Order, target: ShippingBoardStageKey): ShippingBoardAction | null {
  if (target === "waiting" && (order.status === "arrived" || order.status === "loading")) {
    return "rewind_loading";
  }
  if (
    target === "exit" &&
    (order.status === "loading" || (order.status === "arrived" && order.transport_type !== "train"))
  ) {
    return "finish_loading";
  }
  if (target === "done" && order.status === "loaded") return "ship";
  return null;
}

/** Единственный HTTP-переход из `loaded` в `shipped`. */
export function postLoadedOrderExit(orderId: number, post: PostRequest): Promise<unknown> {
  return post(`/orders/${orderId}/ship/`, {});
}
