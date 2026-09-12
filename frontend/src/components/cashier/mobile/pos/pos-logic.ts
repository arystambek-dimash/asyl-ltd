import { availableCents, blockingStore, type DebtStore } from "@/lib/debt-orders";
import type { Order, Payment } from "@/lib/types";

export type PosTab = "qr" | "remote" | "history";
export type PosFlow = Exclude<PosTab, "history">;
export type PosStep = "client" | "order" | "amount" | "phone" | "result";

export interface PosState {
  /** Видимый таб нижней панели. */
  tab: PosTab;
  /** Режим текущей оплаты: QR на экране кассира или счёт на телефон клиента. */
  flow: PosFlow;
  step: PosStep;
  clientId: number | null;
  clientName: string;
  orderId: number | null;
  /** Сумма целыми тенге, цифрами; "" — ноль. */
  amount: string;
  phone: string;
  /** Выданная оплата (QR или счёт) — её статус опрашивается. */
  payment: Payment | null;
  error: string;
}

export const INITIAL_POS_STATE: PosState = {
  tab: "qr",
  flow: "qr",
  step: "client",
  clientId: null,
  clientName: "",
  orderId: null,
  amount: "",
  phone: "",
  payment: null,
  error: "",
};

export type PosAction =
  | { type: "tab"; tab: PosTab }
  | { type: "client"; id: number; name: string }
  | { type: "order"; id: number; amount: string }
  | { type: "amount"; amount: string }
  | { type: "phone-step"; phone: string }
  | { type: "phone"; phone: string }
  | { type: "issued"; payment: Payment }
  | { type: "payment"; payment: Payment }
  | { type: "error"; error: string }
  | { type: "poll-error"; paymentId: number; error: string }
  | { type: "back" }
  | { type: "retry" }
  | { type: "reset" };

const MAX_DIGITS = 12;
const CLOSED_PROVIDER_STATUSES = new Set(["expired", "cancelled", "error", "superseded"]);

/** Цифра с клавиатуры: без ведущего нуля и не больше доступного. */
export function appendDigit(amount: string, digit: string, max: number): string {
  if (!/^\d$/.test(digit)) return amount;
  const next = amount === "" ? (digit === "0" ? "" : digit) : amount + digit;
  if (next.length > MAX_DIGITS || Number(next || "0") > max) return amount;
  return next;
}

export function eraseDigit(amount: string): string {
  return amount.slice(0, -1);
}

/** Сколько целых тенге можно взять по QR и сколько тиынов останется на другой способ. */
export function wholeTengeLimit(order: Order): { max: number; tiyn: number } {
  const cents = availableCents(order);
  return { max: Math.floor(cents / 100), tiyn: cents % 100 };
}

/** Почему заказ нельзя оплатить в POS; null — можно. */
export function posOrderBlock(order: Order, stores: readonly DebtStore[]): string | null {
  if (order.currency !== "KZT") return "QR только в тенге";
  const store = blockingStore(order, stores);
  if (store) return `Оплата для магазина «${store.name}» сегодня недоступна`;
  if (wholeTengeLimit(order).max < 1) {
    return (order.pending_payments ?? []).length > 0 ? "Всё уже ожидает подтверждения" : "Нечего оплачивать";
  }
  return null;
}

export type PaymentOutcome = "waiting" | "paid" | "failed";

/** Итог выданной оплаты: деньги пришли, QR/счёт закрыт без денег или ждём клиента. */
export function paymentOutcome(payment: Payment): PaymentOutcome {
  if (payment.status === "confirmed") return "paid";
  if (payment.status === "rejected" || (payment.provider && CLOSED_PROVIDER_STATUSES.has(payment.provider.status))) {
    return "failed";
  }
  return "waiting";
}

function freshState(flow: PosFlow): PosState {
  return { ...INITIAL_POS_STATE, tab: flow, flow };
}

export function posReducer(state: PosState, action: PosAction): PosState {
  switch (action.type) {
    case "tab": {
      if (action.tab === "history") return { ...state, tab: "history" };
      if (action.tab === state.flow) return { ...state, tab: action.tab };
      // Выданный QR или счёт не переносится в другой режим — начинаем новую оплату.
      if (state.step === "result") return freshState(action.tab);
      return {
        ...state,
        tab: action.tab,
        flow: action.tab,
        step: state.step === "phone" ? "amount" : state.step,
        error: "",
      };
    }
    case "client":
      return {
        ...state,
        step: "order",
        clientId: action.id,
        clientName: action.name,
        orderId: null,
        amount: "",
        phone: "",
        payment: null,
        error: "",
      };
    case "order":
      return { ...state, step: "amount", orderId: action.id, amount: action.amount, error: "" };
    case "amount":
      return { ...state, amount: action.amount, error: "" };
    case "phone-step":
      return { ...state, step: "phone", phone: state.phone || action.phone, error: "" };
    case "phone":
      return { ...state, phone: action.phone, error: "" };
    case "issued":
      return { ...state, step: "result", payment: action.payment, error: "" };
    case "payment":
      return state.payment && state.payment.id === action.payment.id
        ? { ...state, payment: action.payment, error: "" }
        : state;
    case "error":
      return { ...state, error: action.error };
    case "poll-error":
      // Ошибка опроса относится только к той оплате, которая сейчас на экране.
      return state.step === "result" && state.payment?.id === action.paymentId
        ? { ...state, error: action.error }
        : state;
    case "back":
      switch (state.step) {
        case "order":
          return { ...state, step: "client", clientId: null, clientName: "", orderId: null, amount: "", error: "" };
        case "amount":
          return { ...state, step: "order", orderId: null, amount: "", error: "" };
        case "phone":
          return { ...state, step: "amount", error: "" };
        case "result":
          return freshState(state.flow);
        default:
          return state;
      }
    case "retry":
      return { ...state, step: "amount", payment: null, error: "" };
    case "reset":
      return freshState(state.flow);
  }
}
