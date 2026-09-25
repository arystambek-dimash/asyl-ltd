"use client";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import type { ClientDebtDetail } from "@/lib/debt-orders";
import { eraseAmount, pressAmountDigit } from "@/lib/payment-amount";
import type { Payment } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import {
  INITIAL_POS_STATE,
  freshState,
  paymentOutcome,
  posOrderBlock,
  posReducer,
  wholeTengeLimit,
  type PosAction,
  type PosFlow,
  type PosTab,
} from "./pos-logic";

/** Как часто спрашиваем статус выданного QR или счёта. */
const POS_POLL_MS = 3_000;

/**
 * Сценарий POS: клиент → заказ → сумма → QR или счёт, опрос статуса до «Оплачено».
 * `entry` — вкладка, которую открывает адрес (?view=pos — «Оплата», ?view=remote — «Удаленно»;
 * null вне POS); дальше вкладки переключает панель внизу POS. `department` — отдел из шапки
 * кассы: список должников уже отфильтрован по нему, заказы клиента фильтруем здесь.
 */
export function usePosFlow({
  entry,
  onPaid,
  department = null,
}: {
  entry: PosFlow | null;
  onPaid?: () => void;
  department?: string | null;
}) {
  // Первый кадр уже на вкладке из адреса — без мигания «POS» при входе по ?view=remote.
  const [state, dispatch] = useReducer(posReducer, entry, (initial) =>
    initial ? freshState(initial) : INITIAL_POS_STATE,
  );
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const detail = useApi<ClientDebtDetail>(state.clientId ? `/clients/${state.clientId}/debt-detail/` : null);
  const { reload: reloadDetail } = detail;
  const orders = useMemo(
    () => (detail.data?.orders ?? []).filter((row) => !department || row.department === department),
    [department, detail.data],
  );
  const order = orders.find((row) => row.id === state.orderId) ?? null;
  const block = order ? posOrderBlock(order, detail.data?.stores ?? []) : null;
  const limit = order ? wholeTengeLimit(order).max : 0;
  const payment = state.payment;
  const outcome = payment ? paymentOutcome(payment) : null;

  const issue = useCallback(
    async (body: Record<string, string>) => {
      // Ref, а не только state: второй тап в том же тике не должен создать второй QR/резерв.
      if (!state.orderId || inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      try {
        const response = await api.post<Payment>(`/orders/${state.orderId}/payments/`, body);
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

  // Вход в POS: адрес задаёт вкладку (редьюсер сам не трогает начатую оплату). Пока запрос
  // в полёте, вкладку не меняем — ответ должен лечь туда, откуда его отправили.
  useEffect(() => {
    if (entry && !inFlight.current) dispatch({ type: "enter", tab: entry });
  }, [entry]);

  const poll = useCallback(async () => {
    if (!payment) return;
    try {
      const response = await api.get<Payment>(`/orders/${payment.order}/payments/${payment.id}/`);
      dispatch({ type: "payment", payment: response.data });
    } catch (e) {
      dispatch({ type: "poll-error", paymentId: payment.id, error: apiError(e) });
    }
  }, [payment]);
  // Опрашиваем только пока POS на экране: брошенный QR не должен дёргать сервер с главной.
  useVisiblePolling(poll, POS_POLL_MS, entry !== null && state.step === "result" && outcome === "waiting");

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
    orders,
    order,
    block,
    outcome,
    canGoBack: state.tab === "history" || state.step !== "client",
    setTab: (tab: PosTab) => whenIdle({ type: "tab", tab }),
    pickClient: (id: number, name: string) => whenIdle({ type: "client", id, name }),
    pickOrder: (id: number) => {
      const target = orders.find((row) => row.id === id);
      const max = target ? wholeTengeLimit(target).max : 0;
      whenIdle({ type: "order", id, amount: max > 0 ? String(max) : "" });
    },
    digit: (digit: string) => dispatch({ type: "amount", amount: pressAmountDigit(state.amount, digit, limit) }),
    erase: () => dispatch({ type: "amount", amount: eraseAmount(state.amount) }),
    fillAll: () => dispatch({ type: "amount", amount: limit > 0 ? String(limit) : "" }),
    toPhone: () => dispatch({ type: "phone-step", phone: detail.data?.client.phone ?? "" }),
    setPhone: (phone: string) => dispatch({ type: "phone", phone }),
    issueQr: () => void issue({ method: "kaspi", channel: "qr", amount: state.amount }),
    sendInvoice: () => void issue({ method: "invoice", amount: state.amount, phone_number: state.phone }),
    // «‹» из «Истории» возвращает к начатой оплате, со ступени — на ступень назад.
    back: () => whenIdle(state.tab === "history" ? { type: "tab", tab: state.flow } : { type: "back" }),
    retry: () => whenIdle({ type: "retry" }),
    reset: () => whenIdle({ type: "reset" }),
  };
}

export type PosFlowApi = ReturnType<typeof usePosFlow>;
