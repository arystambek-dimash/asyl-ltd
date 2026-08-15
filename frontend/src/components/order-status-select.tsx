"use client";

import { ChevronDown } from "lucide-react";
import { ORDER_MANUAL_STATUSES, ORDER_STATUS_LABELS, orderStatusGroup } from "@/lib/constants";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<string, string> = {
  pending: "border-slate-200 bg-slate-100 text-slate-700",
  confirmed: "border-amber-200 bg-amber-50 text-amber-700",
  loaded: "border-blue-200 bg-blue-50 text-blue-700",
  shipped: "border-emerald-200 bg-emerald-50 text-emerald-700",
  cancelled: "border-red-200 bg-red-50 text-red-700",
};
const STATUS_DOT: Record<string, string> = {
  pending: "bg-slate-500",
  confirmed: "bg-amber-500",
  loaded: "bg-blue-500",
  shipped: "bg-emerald-500",
  cancelled: "bg-red-500",
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
  return (
    <label
      className={cn("relative inline-flex max-w-full items-center", className)}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <span className="sr-only">Изменить статус заказа</span>
      <span
        className={cn(
          "pointer-events-none absolute left-3 z-10 size-2 rounded-full",
          STATUS_DOT[current] ?? STATUS_DOT.pending,
        )}
      />
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
          STATUS_STYLE[current] ?? STATUS_STYLE.pending,
        )}
      >
        {!ORDER_MANUAL_STATUSES.some((option) => option === current) && (
          <option value={current} disabled>
            {ORDER_STATUS_LABELS[current] ?? current}
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
