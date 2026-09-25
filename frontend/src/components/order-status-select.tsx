"use client";

import { ChevronDown } from "lucide-react";
import {
  ORDER_MANUAL_STATUSES,
  ORDER_STATUS_LABELS,
  orderStatusGroup,
  orderStatusLabel,
  orderStatusTone,
  type BadgeTone,
} from "@/lib/constants";
import { cn } from "@/lib/utils";

// Цвет статуса — из общей карты тонов (как у StatusBadge); здесь только его
// оформление для крупного селекта.
const TONE_STYLE: Record<BadgeTone, { box: string; dot: string }> = {
  muted: { box: "border-slate-200 bg-slate-100 text-slate-700", dot: "bg-slate-500" },
  outline: { box: "border-slate-200 bg-slate-100 text-slate-700", dot: "bg-slate-500" },
  warning: { box: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  primary: { box: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  success: { box: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  destructive: { box: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
};

export function OrderStatusSelect({
  status,
  disabled,
  onChange,
  className,
}: {
  status: string;
  disabled?: boolean;
  onChange: (status: string) => void;
  className?: string;
}) {
  const current = orderStatusGroup(status);
  const style = TONE_STYLE[orderStatusTone(current)];
  return (
    <label
      className={cn("relative inline-flex max-w-full items-center", className)}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <span className="sr-only">Изменить статус заказа</span>
      <span className={cn("pointer-events-none absolute left-3 z-10 size-2 rounded-full", style.dot)} />
      <select
        aria-label="Статус заказа"
        value={current}
        disabled={disabled}
        onChange={(event) => {
          const next = event.target.value;
          if (next !== current) onChange(next);
        }}
        className={cn(
          // На телефоне это самый нажимаемый элемент списка, и жмут его в цехе
          // перчаткой: 32px мимо, 44px попадает. На десктопе размер прежний.
          "h-11 max-w-full appearance-none rounded-lg border py-1 pl-7 pr-8 text-xs font-semibold outline-none transition focus:ring-2 focus:ring-blue-500/25 disabled:cursor-wait disabled:opacity-60 sm:h-8",
          style.box,
        )}
      >
        {!ORDER_MANUAL_STATUSES.some((option) => option === current) && (
          <option value={current} disabled>
            {orderStatusLabel(current)}
          </option>
        )}
        {ORDER_MANUAL_STATUSES.map((option) => (
          <option key={option} value={option}>
            {ORDER_STATUS_LABELS[option]}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 size-3.5 opacity-60" />
    </label>
  );
}
