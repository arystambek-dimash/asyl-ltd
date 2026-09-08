"use client";

import { useCallback, useState } from "react";
import { formatPlate } from "@/components/ui/license-plate-input";
import { api, apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import {
  allowedShippingBoardStages,
  postLoadedOrderExit,
  shippingBoardAction,
  type ShippingBoardStageKey,
  type ShippingCapabilities,
} from "@/lib/shipping-flow";
import type { AiCountingSession, Order } from "@/lib/types";

/** Итог действия: ошибка показывается той модалкой, которая действие вызвала. */
export interface ShippingActionResult {
  ok: boolean;
  /** Пустая строка при успехе и при 403 (его уже показал интерцептор). */
  error: string;
}

export interface ShippingConfirmText {
  title: string;
  description: string;
  confirmLabel: string;
  confirmVariant: "default" | "destructive";
}

/* ── Тексты подтверждений ─────────────────────────────────────────────── */

/** Счёт мешков виден в подтверждении: если промахнулись мимо «+1»,
 * ошибка заметна здесь до завершения погрузки. */
export function finishLoadingConfirmText(order: Order, bags: number): ShippingConfirmText {
  const ordered = orderedBagCount(order);
  return {
    title: "Завершить погрузку?",
    description:
      order.transport_type === "train"
        ? `В вагон загружено ${bags} из ${ordered} меш. После завершения результат будет готов к отдельному оформлению выезда.`
        : `Для машины ${formatPlate(order.truck_number) || "без номера"} зафиксировано ${bags} из ${ordered} меш. После завершения заказ перейдёт в «Готов к выезду», но выезд ещё не будет оформлен.`,
    confirmLabel: "Завершить погрузку",
    confirmVariant: "default",
  };
}

export function shipOutConfirmText(order: Pick<Order, "id">): ShippingConfirmText {
  return {
    title: "Оформить выезд?",
    description: `Погрузка заказа #${order.id} уже завершена. Подтвердите, что транспорт действительно покинул пост.`,
    confirmLabel: "Подтвердить выезд",
    confirmVariant: "default",
  };
}

/* ── Хук действий ─────────────────────────────────────────────────────── */

export interface ShippingActionsOptions {
  sessionsByOrderId: Map<number, AiCountingSession>;
  capabilities: ShippingCapabilities;
  reloadOrders: () => Promise<unknown>;
  reloadSessions: () => Promise<unknown>;
  /** Только при shipping.view — иначе история не запрашивается. */
  reloadHistories?: () => Promise<unknown>;
}

/**
 * Единый исполнитель действий строки: один `busyOrderId` на таблицу,
 * обязательная перезагрузка заказов и сессий после любого исхода (сервер мог
 * закрепить слот даже когда ПК камеры не ответил), ошибка — вызывающему.
 */
export function useShippingActions({
  sessionsByOrderId,
  capabilities,
  reloadOrders,
  reloadSessions,
  reloadHistories,
}: ShippingActionsOptions) {
  const [busyOrderId, setBusyOrderId] = useState<number | null>(null);

  const act = useCallback(
    async (
      orderId: number,
      fn: () => Promise<unknown>,
      options: { history?: boolean } = {},
    ): Promise<ShippingActionResult> => {
      setBusyOrderId(orderId);
      let result: ShippingActionResult = { ok: true, error: "" };
      try {
        await fn();
      } catch (cause) {
        result = { ok: false, error: apiError(cause) };
      } finally {
        try {
          await Promise.all([
            reloadOrders(),
            reloadSessions(),
            ...(options.history && reloadHistories ? [reloadHistories()] : []),
          ]);
        } finally {
          setBusyOrderId((current) => (current === orderId ? null : current));
        }
      }
      return result;
    },
    [reloadHistories, reloadOrders, reloadSessions],
  );

  const stopOrderAi = useCallback(
    async (order: Order, completeOrder = false) => {
      const session = sessionsByOrderId.get(order.id);
      if (!session) return false;
      await api.delete(`/cameras/${session.camera}/ai/`, {
        params: { order_id: order.id, session_id: session.id, complete_order: completeOrder ? 1 : 0 },
        data: { order_id: order.id, session_id: session.id, complete_order: completeOrder },
      });
      return true;
    },
    [sessionsByOrderId],
  );

  const completeLoading = useCallback(
    async (order: Order, latestBags = order.bags_loaded ?? 0) => {
      // При активной AI-сессии один backend-вызов фиксирует финальный кадр/счёт,
      // закрывает сессию заказа и переводит его только в `loaded`. Оформление
      // физического выезда остаётся отдельным действием с правом shipping.ship.
      if (await stopOrderAi(order, true)) return;
      if (order.transport_type === "train") {
        await api.post(`/orders/${order.id}/train/`, { action: "finish" });
        return;
      }
      if (order.status === "arrived") {
        await api.post(`/orders/${order.id}/load/`, { bags: latestBags });
      }
      if (order.status === "arrived" || order.status === "loading") {
        await api.post(`/orders/${order.id}/finish-loading/`, {});
      }
    },
    [stopOrderAi],
  );

  // Куда пишется счёт мешков: машина — /load/, вагон — train count.
  const saveBags = useCallback(
    (order: Order) => (bags: number) =>
      order.transport_type === "train"
        ? api.post(`/orders/${order.id}/train/`, { action: "count", bags })
        : api.post(`/orders/${order.id}/load/`, { bags }),
    [],
  );

  const allowedStages = useCallback(
    (order: Order): ShippingBoardStageKey[] => allowedShippingBoardStages(order, capabilities),
    [capabilities],
  );

  const executeMove = useCallback(
    async (order: Order, target: ShippingBoardStageKey, latestBags?: number): Promise<ShippingActionResult> => {
      if (!allowedStages(order).includes(target)) return { ok: false, error: "" };
      const action = shippingBoardAction(order, target);
      if (!action) return { ok: false, error: "" };
      return act(
        order.id,
        async () => {
          if (action === "rewind_loading") {
            await stopOrderAi(order);
            await api.post(`/orders/${order.id}/rewind-loading/`, {});
          } else if (action === "finish_loading") {
            await completeLoading(order, latestBags);
          } else {
            // `loaded` — ещё не выехал. Это единственный штатный переход в
            // `shipped`, и он защищён отдельным правом shipping.ship.
            await postLoadedOrderExit(order.id, (url, body) => api.post(url, body));
          }
        },
        { history: true },
      );
    },
    [act, allowedStages, completeLoading, stopOrderAi],
  );

  // Завершение сессии привязано к её id: запоздавшая команда не должна
  // закрыть новую сессию этой же камеры. Параметры дублируем в query и JSON.
  const sessionRequest = useCallback((session: AiCountingSession) => {
    const body = { order_id: session.order_id, session_id: session.id };
    return { body, config: { params: body } };
  }, []);

  const stopSessionAi = useCallback(
    (session: AiCountingSession, completeOrder: boolean) => {
      const { body, config } = sessionRequest(session);
      return act(
        session.order_id,
        () =>
          api.delete(`/cameras/${session.camera}/ai/`, {
            params: { ...config.params, complete_order: completeOrder ? 1 : 0 },
            data: { ...body, complete_order: completeOrder },
          }),
        { history: completeOrder },
      );
    },
    [act, sessionRequest],
  );

  return {
    busyOrderId,
    act,
    allowedStages,
    stopOrderAi,
    completeLoading,
    saveBags,
    executeMove,
    stopSessionAi,
  };
}

export type ShippingActions = ReturnType<typeof useShippingActions>;
