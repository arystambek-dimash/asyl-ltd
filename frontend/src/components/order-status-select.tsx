"use client";

import { ChevronDown } from "lucide-react";
import { ORDER_PUBLIC_STATUSES, ORDER_STATUS_LABELS, orderStatusGroup } from "@/lib/constants";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<string, string> = {
  pending: "border-[var(--border)] bg-[var(--muted)] text-[var(--foreground)]",
  confirmed: "border-[var(--soft-amber-border)] bg-[var(--soft-amber)] text-[var(--soft-amber-foreground)]",
  shipped: "border-[var(--soft-green-border)] bg-[var(--soft-green)] text-[var(--soft-green-foreground)]",
  cancelled: "border-[var(--soft-red-border)] bg-[var(--soft-red)] text-[var(--soft-red-foreground)]",
};
const STATUS_DOT: Record<string, string> = {
  pending: "bg-[var(--muted-foreground)]",
  confirmed: "bg-[var(--warning)]",
  shipped: "bg-[var(--success)]",
  cancelled: "bg-[var(--destructive)]",
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
          "h-11 max-w-full appearance-none rounded-lg border py-1 pl-7 pr-8 text-xs font-semibold outline-none transition focus:ring-2 focus:ring-[var(--ring)] disabled:cursor-wait disabled:opacity-60 sm:h-8",
          STATUS_STYLE[current] ?? STATUS_STYLE.pending,
        )}
      >
        {ORDER_PUBLIC_STATUSES.map((option) => (
          <option key={option} value={option}>
            {ORDER_STATUS_LABELS[option]}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 size-3.5 opacity-60" />
    </label>
  );
}
