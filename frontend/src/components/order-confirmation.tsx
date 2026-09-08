"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { formatCurrency } from "@/lib/utils";
import type { Department, Order } from "@/lib/types";

export interface OrderConfirmationData {
  department: string;
  prices: Record<string, string>;
}

/** Shared by the cashier and order page: confirmation is one atomic request. */
export function OrderConfirmation({
  order,
  departments,
  busy,
  onConfirm,
}: {
  order: Order;
  departments: Department[];
  busy: boolean;
  onConfirm: (data: OrderConfirmationData) => void;
}) {
  // Even legacy requests may contain a department assigned by the old default.
  const [department, setDepartment] = useState("");
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      order.items.map((item) => [String(item.id), String(item.unit_price ?? item.client_price ?? "")]),
    ),
  );
  const valid =
    department &&
    order.items.every((item) => {
      const value = Number(prices[String(item.id)]);
      return Number.isFinite(value) && value > 0;
    });
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !busy) onConfirm({ department, prices });
      }}
    >
      <label className="grid gap-1.5 text-sm font-medium">
        Отдел продаж
        <Select value={department} onChange={(event) => setDepartment(event.target.value)} required disabled={busy}>
          <option value="">Выберите отдел продаж</option>
          {departments
            .filter((row) => row.is_active !== false)
            .map((row) => (
              <option key={row.code} value={row.code}>
                {row.name}
              </option>
            ))}
        </Select>
      </label>
      {!departments.length && (
        <p role="status" className="text-sm text-[var(--muted-foreground)]">
          Нет доступных отделов. Проверьте справочник отделов продаж.
        </p>
      )}
      {order.items.map((item) => (
        <div key={item.id} className="grid gap-2 border-t pt-3 sm:grid-cols-[1fr_150px_120px] sm:items-end">
          <div className="text-sm">
            <div className="font-medium">{item.product_label || `Товар #${item.product}`}</div>
            <div className="text-[var(--muted-foreground)]">{item.quantity} меш.</div>
          </div>
          <label className="grid gap-1 text-xs">
            Цена за мешок
            <Input
              aria-label={`Цена: ${item.product_label || item.product}`}
              type="number"
              step="0.01"
              min="0.01"
              required
              disabled={busy}
              value={prices[String(item.id)] ?? ""}
              onChange={(event) => setPrices((current) => ({ ...current, [String(item.id)]: event.target.value }))}
            />
          </label>
          <span className="text-right text-sm font-medium tabular-nums">
            {formatCurrency(Number(prices[String(item.id)] || 0) * Number(item.quantity), order.currency)}
          </span>
        </div>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
        <p className="text-xs text-[var(--muted-foreground)]">Проверьте отдел и цены перед подтверждением.</p>
        <Button type="submit" disabled={busy || !valid}>
          {busy ? "Подтверждение…" : "Подтвердить заказ"}
        </Button>
      </div>
    </form>
  );
}
