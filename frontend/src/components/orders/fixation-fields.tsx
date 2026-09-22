"use client";

import { CalendarClock, Info } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { cn, todayLocalIsoDate } from "@/lib/utils";

/** Фиксация статуса и оплаты задним числом — общий блок формы и модалки. */
export type FixationStatus = "" | "confirmed" | "shipped";
export type FixationPaymentMethod = "cash" | "kaspi" | "remote";

export interface FixationDraft {
  date: string;
  status: FixationStatus;
  paid: boolean;
  paymentMethod: FixationPaymentMethod;
}

export const FIXATION_STATUS_OPTIONS: { value: FixationStatus; label: string; caption: string }[] = [
  { value: "confirmed", label: "Ожидает загрузки", caption: "Подтверждён, но не отгружен" },
  { value: "shipped", label: "Отгружено", caption: "Склад не списывается" },
];

export const FIXATION_METHOD_OPTIONS: { value: FixationPaymentMethod; label: string }[] = [
  { value: "cash", label: "Наличные" },
  { value: "kaspi", label: "Kaspi" },
  { value: "remote", label: "Удалённо" },
];

export function emptyFixationDraft(): FixationDraft {
  return { date: todayLocalIsoDate(), status: "", paid: false, paymentMethod: "cash" };
}

/** Тело запроса для `backdate` при создании и для `POST /orders/{id}/fixate/`. */
export function fixationBody(draft: FixationDraft) {
  return {
    date: draft.date,
    status: draft.status || null,
    paid: draft.paid,
    payment_method: draft.paymentMethod,
  };
}

export function fixationDraftError(draft: FixationDraft, { shippedAlready }: { shippedAlready: boolean }) {
  if (!draft.date) return "Укажите дату.";
  if (draft.date > todayLocalIsoDate()) return "Дата не может быть в будущем.";
  if (draft.paid && draft.status !== "shipped" && !shippedAlready) {
    return "Оплату можно зафиксировать только у отгруженного заказа.";
  }
  return "";
}

export function FixationFields({
  draft,
  onChange,
  canPay,
  /** Заказ уже отгружен: статус не меняем, фиксируем только оплату. */
  shippedAlready = false,
  idPrefix = "fixation",
}: {
  draft: FixationDraft;
  onChange: (draft: FixationDraft) => void;
  canPay: boolean;
  shippedAlready?: boolean;
  idPrefix?: string;
}) {
  const update = (patch: Partial<FixationDraft>) => onChange({ ...draft, ...patch });
  const paymentAllowed = canPay && (draft.status === "shipped" || shippedAlready);

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
        <div className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-date`}>Дата</Label>
          <div className="relative">
            <CalendarClock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <Input
              id={`${idPrefix}-date`}
              type="date"
              max={todayLocalIsoDate()}
              value={draft.date}
              onChange={(event) => update({ date: event.target.value })}
              className="h-10 rounded-xl pl-9 tabular-nums"
            />
          </div>
        </div>
        {!shippedAlready && (
          <div className="grid gap-1.5">
            <Label>Статус</Label>
            <Segmented
              ariaLabel="Статус заказа"
              value={draft.status}
              options={FIXATION_STATUS_OPTIONS}
              onChange={(status) => update({ status, paid: status === "shipped" ? draft.paid : false })}
            />
          </div>
        )}
      </div>

      {canPay && (
        <div className="grid gap-2">
          <label
            className={cn(
              "flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 transition",
              draft.paid ? "border-emerald-300 bg-emerald-50/70" : "border-slate-200 bg-white",
              !paymentAllowed && "cursor-not-allowed opacity-60",
            )}
          >
            <input
              type="checkbox"
              className="size-4 accent-emerald-600"
              checked={draft.paid}
              disabled={!paymentAllowed}
              onChange={(event) => update({ paid: event.target.checked })}
            />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-slate-900">Оплачен полностью</span>
              <span className="block text-[11px] text-slate-500">
                {paymentAllowed
                  ? "Оплата на всю сумму той же датой, сразу подтверждена кассой"
                  : "Доступно только для отгруженного заказа"}
              </span>
            </span>
          </label>
          {draft.paid && (
            <Segmented
              ariaLabel="Способ оплаты"
              value={draft.paymentMethod}
              options={FIXATION_METHOD_OPTIONS}
              onChange={(paymentMethod) => update({ paymentMethod })}
            />
          )}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-xs text-amber-900">
        <Info className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
        <span>
          Всё проставится указанной датой и попадёт в журнал с пометкой «задним числом». Склад при этом не списывается —
          остатки считаются уже сверенными.
        </span>
      </div>
    </div>
  );
}
