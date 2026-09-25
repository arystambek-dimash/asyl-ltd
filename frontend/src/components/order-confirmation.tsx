"use client";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TransportNumberFields } from "@/components/ui/transport-number-fields";
import { formatEstimate, requestEstimate } from "@/lib/orders";
import { transportChanges, transportNumberError, transportPairOf, type TransportPair } from "@/lib/plates";
import type { Department, Order } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { formatCurrency, PHONE_INPUT_TEXT } from "@/lib/utils";
import { orderTransportText } from "@/lib/wagons";
import { useAuth } from "@/store/auth";

export interface OrderConfirmationData {
  department: string;
  prices: Record<string, string>;
  /** Только урезанные позиции: {id позиции: мешков}. */
  quantities?: Record<string, number>;
  truck_number?: string;
  trailer_number?: string;
}

/** GET /orders/{id}/confirm-context/: остаток на складе заказа и можно ли менять номер. */
export interface ConfirmContext {
  items: Record<string, { on_hand: number; awaiting_shipment: number }>;
  /** Номер указал клиент — сотрудник его не меняет. */
  transport_locked: boolean;
  client_country: string;
}

interface OrderConfirmationProps {
  order: Order;
  departments: Department[];
  busy: boolean;
  /** Ошибка подтверждения — у кнопки, чтобы длинная заявка её не прятала. */
  error?: string;
  onConfirm: (data: OrderConfirmationData) => void;
}

/** Форма подтверждения заявки (карточка заказа и вкладка «Заявки»): подтверждение — один атомарный запрос. */
export function OrderConfirmation(props: OrderConfirmationProps) {
  // Состав заявки перечитали (400 invalid_item): окно заполняется заново по новым позициям.
  const composition = props.order.items.map((item) => item.id).join(".");
  return <ConfirmationForm key={composition} {...props} />;
}

function ConfirmationForm({ order, departments, busy, error = "", onConfirm }: OrderConfirmationProps) {
  const { me } = useAuth();
  const fieldId = useId();
  const {
    data: context,
    error: contextError,
    reload: retryContext,
  } = useApi<ConfirmContext>(`/orders/${order.id}/confirm-context/`);
  // Клиент без отдела закрепится за отделом подтверждения; сотрудник отдела — только за своим.
  const unassigned = !order.client_department;
  const lockedDepartment = order.client_department || me?.sales_department?.code || "";
  const [department, setDepartment] = useState(lockedDepartment);
  const [askAssign, setAskAssign] = useState(false);
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      order.items.map((item) => [String(item.id), String(item.unit_price ?? item.client_price ?? "")]),
    ),
  );
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(order.items.map((item) => [String(item.id), String(item.quantity)])),
  );
  const train = order.transport_type === "train";
  const savedNumbers = transportPairOf(order);
  const [numbers, setNumbers] = useState<TransportPair>(savedNumbers);
  const transportLocked = context?.transport_locked === true;

  const quantityValid = (item: Order["items"][number]) => {
    const value = Number(quantities[String(item.id)]);
    return Number.isInteger(value) && value >= 1 && value <= Number(item.quantity);
  };
  const changedQuantities = Object.fromEntries(
    order.items
      .filter((item) => quantityValid(item) && Number(quantities[String(item.id)]) !== Number(item.quantity))
      .map((item) => [String(item.id), Number(quantities[String(item.id)])]),
  );
  // Номер в окне необязателен: уходит только исправленный и непустой.
  const changedNumbers: Partial<TransportPair> = transportLocked
    ? {}
    : Object.fromEntries(
        Object.entries(transportChanges(savedNumbers, train ? { ...numbers, trailer_number: "" } : numbers)).filter(
          ([, value]) => value,
        ),
      );
  // Номер, который API не примет, помечается у своего поля: кнопка не гаснет молча.
  const numberError = (field: keyof TransportPair) => {
    const value = changedNumbers[field];
    return value ? transportNumberError(value, order.transport_type) : null;
  };
  const truckError = numberError("truck_number");
  const trailerError = numberError("trailer_number");
  const numbersValid = !truckError && !trailerError;

  const requested = requestEstimate(order.items);
  const estimate = requestEstimate(order.items, { prices, quantities });
  const cut = estimate.bags < requested.bags;
  const bagsLabel = cut ? `${estimate.bags} из ${requested.bags} меш.` : `${estimate.bags} меш.`;
  const valid =
    departments.some((row) => row.code === department && row.is_active !== false) &&
    order.items.every((item) => {
      const value = Number(prices[String(item.id)]);
      return Number.isFinite(value) && value > 0 && quantityValid(item);
    }) &&
    numbersValid;

  function payload(): OrderConfirmationData {
    return {
      department,
      prices,
      ...(Object.keys(changedQuantities).length ? { quantities: changedQuantities } : {}),
      ...changedNumbers,
    };
  }

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid || busy) return;
        if (unassigned && !askAssign) setAskAssign(true);
        else onConfirm(payload());
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
      {order.items.map((item) => {
        const key = String(item.id);
        const label = item.product_label || `Товар #${item.product}`;
        const line = requestEstimate([item], { prices, quantities });
        const stock = context?.items?.[key];
        const free = stock ? Math.max(0, stock.on_hand - stock.awaiting_shipment) : null;
        const short = free !== null && quantityValid(item) && Number(quantities[key]) > free;
        return (
          <div key={item.id} className="grid gap-2 border-t pt-3">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 font-medium">{label}</span>
              <span className="shrink-0 text-xs text-[var(--muted-foreground)]">Запрошено {item.quantity} меш.</span>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-[110px_150px_1fr] sm:items-end">
              <label className="grid gap-1 text-xs">
                Количество, меш.
                <Input
                  aria-label={`Количество: ${item.product_label || item.product}`}
                  type="number"
                  inputMode="numeric"
                  step="1"
                  min="1"
                  max={item.quantity}
                  required
                  disabled={busy}
                  aria-invalid={!quantityValid(item) || undefined}
                  value={quantities[key] ?? ""}
                  onChange={(event) => setQuantities((current) => ({ ...current, [key]: event.target.value }))}
                  className={PHONE_INPUT_TEXT}
                />
              </label>
              <label className="grid gap-1 text-xs">
                Цена за мешок
                <Input
                  aria-label={`Цена: ${item.product_label || item.product}`}
                  type="number"
                  step="0.01"
                  min="0.01"
                  required
                  disabled={busy}
                  value={prices[key] ?? ""}
                  onChange={(event) => setPrices((current) => ({ ...current, [key]: event.target.value }))}
                  className={PHONE_INPUT_TEXT}
                />
              </label>
              <span className="col-span-2 text-right text-sm font-medium tabular-nums sm:col-span-1">
                {line.amount === null ? "—" : formatCurrency(line.amount, order.currency)}
              </span>
            </div>
            {stock && (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-[var(--muted-foreground)]">
                  На складе {stock.on_hand} · ждут отгрузки {stock.awaiting_shipment}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  // Ноль мешков не подтверждают: позицию без остатка отклоняют заявкой.
                  disabled={busy || stock.on_hand <= 0}
                  onClick={() =>
                    setQuantities((current) => ({
                      ...current,
                      [key]: String(Math.min(Number(item.quantity), stock.on_hand)),
                    }))
                  }
                >
                  Отдать сколько есть
                </Button>
              </div>
            )}
            {short && (
              <p
                role="status"
                className="rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-2.5 py-1.5 text-xs text-[var(--warning)]"
              >
                Свободно {free} меш. — может не хватить
              </p>
            )}
          </div>
        );
      })}
      {contextError && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Остаток склада не загрузился.{" "}
          <button type="button" className="underline underline-offset-2" onClick={() => void retryContext()}>
            Повторить
          </button>
        </p>
      )}
      <fieldset className="grid gap-3 border-t pt-3">
        {/* float выводит legend из рамки fieldset: это обычная строка сетки. */}
        <legend className="float-left text-sm font-medium">Транспорт (можно позже)</legend>
        {transportLocked ? (
          <div className="text-sm">
            <div className="font-medium tabular-nums">{orderTransportText(order)}</div>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              Номер указал клиент — изменить его может только он.
            </p>
          </div>
        ) : (
          <TransportNumberFields
            id={fieldId}
            transportType={order.transport_type}
            defaultCountry={context?.client_country}
            disabled={busy}
            errors={{ truck: truckError, trailer: trailerError }}
            value={numbers}
            onChange={setNumbers}
            className="sm:grid-cols-2"
          />
        )}
      </fieldset>
      {/* Итог и кнопка прилипают к низу окна: длинная заявка не прячет их за прокруткой. */}
      <div className="sticky -bottom-4 z-10 -mb-4 grid gap-3 border-t bg-[var(--card)] pb-4 pt-3 sm:-bottom-6 sm:-mb-6 sm:pb-6">
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        <p className="text-sm font-semibold tabular-nums">
          {`Итого: ${bagsLabel} · ${formatEstimate(estimate.amount, order.currency)}`}
        </p>
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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-[var(--muted-foreground)]">
              {numbersValid ? "Проверьте отдел, количество и цены." : "Исправьте номер или оставьте поле пустым."}
            </p>
            <Button type="submit" disabled={busy || !valid}>
              {busy ? "Подтверждение…" : cut ? `Подтвердить ${bagsLabel}` : "Подтвердить заказ"}
            </Button>
          </div>
        )}
      </div>
    </form>
  );
}
