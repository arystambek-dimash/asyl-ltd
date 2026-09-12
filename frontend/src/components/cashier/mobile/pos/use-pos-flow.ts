"use client";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import type { ClientDebtDetail } from "@/lib/debt-orders";
import type { Payment } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import {
  INITIAL_POS_STATE,
  appendDigit,
  eraseDigit,
  paymentOutcome,
  posOrderBlock,
  posReducer,
  wholeTengeLimit,
  type PosAction,
  type PosTab,
} from "./pos-logic";

/** Как часто спрашиваем статус выданного QR или счёта. */
export const POS_POLL_MS = 3_000;

/** Сценарий POS: клиент → заказ → сумма → QR или счёт, опрос статуса до «Оплачено». */
export function usePosFlow({ onPaid }: { onPaid?: () => void } = {}) {
  const [state, dispatch] = useReducer(posReducer, INITIAL_POS_STATE);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const detail = useApi<ClientDebtDetail>(state.clientId ? `/clients/${state.clientId}/debt-detail/` : null);
  const { reload: reloadDetail } = detail;
  const order = detail.data?.orders.find((row) => row.id === state.orderId) ?? null;
  const block = order ? posOrderBlock(order, detail.data?.stores ?? []) : null;
  const limit = order ? wholeTengeLimit(order).max : 0;
  const payment = state.payment;
  const outcome = payment ? paymentOutcome(payment) : null;

  const issue = useCallback(
    async (body: Record<string, string>, expected: "qr" | "phone") => {
      // Ref, а не только state: второй тап в том же тике не должен создать второй QR/резерв.
      if (!state.orderId || inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      try {
        const response = await api.post<Payment>(`/orders/${state.orderId}/payments/`, body);
        // Старый бэкенд без POS-канала записал бы кассовую оплату без QR — «Оплачено» не показываем.
        if (response.data.provider?.channel !== expected) {
          dispatch({
            type: "error",
            error:
              expected === "qr"
                ? "Сервер не выдал Kaspi QR — проверьте оплату в «Истории»."
                : "Сервер не отправил счёт — проверьте оплату в «Истории».",
          });
          void reloadDetail();
          return;
        }
        dispatch({ type: "issued", payment: response.data });
      } catch (e) {
        dispatch({ type: "error", error: apiError(e) });
        // Бэкенд мог успеть создать резерв (неоднозначный сбой провайдера или
        // таймаут запроса) — обновляем доступный остаток, чтобы это стало видно.
        void reloadDetail();
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    },
    [state.orderId, reloadDetail],
  );

  // Пока создаётся QR или счёт, кассир не уходит с шага: ответ должен лечь туда, откуда отправили.
  const whenIdle = (action: PosAction) => {
    if (!inFlight.current) dispatch(action);
  };

  const poll = useCallback(async () => {
    if (!payment) return;
    try {
      const response = await api.get<Payment>(`/orders/${payment.order}/payments/${payment.id}/`);
      dispatch({ type: "payment", payment: response.data });
    } catch (e) {
      dispatch({ type: "poll-error", paymentId: payment.id, error: apiError(e) });
    }
  }, [payment]);
  useVisiblePolling(poll, POS_POLL_MS, state.step === "result" && outcome === "waiting");

  // Деньги пришли — один раз на оплату обновляем долги клиента и общий список.
  const paidFor = useRef<number | null>(null);
  useEffect(() => {
    if (!payment || outcome !== "paid" || paidFor.current === payment.id) return;
    paidFor.current = payment.id;
    void reloadDetail();
    onPaid?.();
  }, [onPaid, outcome, payment, reloadDetail]);

  return {
    state,
    busy,
    detail,
    order,
    block,
    outcome,
    canGoBack: state.tab === "history" || state.step !== "client",
    setTab: (tab: PosTab) => whenIdle({ type: "tab", tab }),
    pickClient: (id: number, name: string) => whenIdle({ type: "client", id, name }),
    pickOrder: (id: number) => {
      const target = detail.data?.orders.find((row) => row.id === id);
      const max = target ? wholeTengeLimit(target).max : 0;
      whenIdle({ type: "order", id, amount: max > 0 ? String(max) : "" });
    },
    digit: (digit: string) => dispatch({ type: "amount", amount: appendDigit(state.amount, digit, limit) }),
    erase: () => dispatch({ type: "amount", amount: eraseDigit(state.amount) }),
    fillAll: () => dispatch({ type: "amount", amount: limit > 0 ? String(limit) : "" }),
    toPhone: () => dispatch({ type: "phone-step", phone: detail.data?.client.phone ?? "" }),
    setPhone: (phone: string) => dispatch({ type: "phone", phone }),
    issueQr: () => void issue({ method: "kaspi", channel: "qr", amount: state.amount }, "qr"),
    sendInvoice: () => void issue({ method: "invoice", amount: state.amount, phone_number: state.phone }, "phone"),
    back: () => whenIdle(state.tab === "history" ? { type: "tab", tab: state.flow } : { type: "back" }),
    retry: () => whenIdle({ type: "retry" }),
    reset: () => whenIdle({ type: "reset" }),
  };
}

export type PosFlowApi = ReturnType<typeof usePosFlow>;
