"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { formatCurrency } from "@/lib/utils";
import type { Department, Order } from "@/lib/types";
import { useAuth } from "@/store/auth";

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
  const { me } = useAuth();
  // Клиент без отдела закрепится за отделом подтверждения; сотрудник отдела — только за своим.
  const unassigned = !order.client_department;
  const lockedDepartment = order.client_department || me?.sales_department?.code || "";
  // Even legacy requests may contain a department assigned by the old default.
  const [department, setDepartment] = useState(lockedDepartment);
  const [askAssign, setAskAssign] = useState(false);
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      order.items.map((item) => [String(item.id), String(item.unit_price ?? item.client_price ?? "")]),
    ),
  );
  const valid =
    departments.some((row) => row.code === department && row.is_active !== false) &&
    order.items.every((item) => {
      const value = Number(prices[String(item.id)]);
      return Number.isFinite(value) && value > 0;
    });
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid || busy) return;
        if (unassigned && !askAssign) setAskAssign(true);
        else onConfirm({ department, prices });
      }}
    >
      <label className="grid gap-1.5 text-sm font-medium">
        Отдел продаж
        <Select
          value={department}
          onChange={(event) => {
            setDepartment(event.target.value);
            setAskAssign(false);
          }}
          required
          disabled={busy || !!lockedDepartment}
        >
          <option value="">Выберите отдел продаж</option>
          {departments
            .filter((row) => row.is_active !== false && (!lockedDepartment || row.code === lockedDepartment))
            .map((row) => (
              <option key={row.code} value={row.code}>
                {row.name}
              </option>
            ))}
        </Select>
      </label>
      {unassigned && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Клиент пока без отдела — при подтверждении закрепится за этим отделом.
        </p>
      )}
      {order.client_department && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Отдел клиента: {order.client_department_name || order.client_department}. Продажа и оплата будут учтены здесь.
        </p>
      )}
      {order.client_department &&
        departments.length > 0 &&
        !departments.some((row) => row.code === order.client_department && row.is_active !== false) && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            Отдел клиента недоступен. Проверьте его в карточке клиента.
          </p>
        )}
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
      {askAssign ? (
        <div
          role="alertdialog"
          aria-labelledby="assign-client-question"
          className="grid gap-3 rounded-lg bg-[var(--muted)] p-3"
        >
          <div>
            <p id="assign-client-question" className="text-sm font-medium">
              Закрепить клиента за отделом «{departments.find((row) => row.code === department)?.name ?? department}»?
            </p>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {order.client_name || "Клиент"} перейдёт в этот отдел: его заказы и оплаты будут учитываться там.
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={() => setAskAssign(false)}>
              Нет
            </Button>
            <Button type="submit" disabled={busy || !valid}>
              {busy ? "Подтверждение…" : "Да, закрепить и подтвердить"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
          <p className="text-xs text-[var(--muted-foreground)]">Проверьте отдел и цены перед подтверждением.</p>
          <Button type="submit" disabled={busy || !valid}>
            {busy ? "Подтверждение…" : "Подтвердить заказ"}
          </Button>
        </div>
      )}
    </form>
  );
}
