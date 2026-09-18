"use client";
import { ArrowLeft, PackageCheck, Printer, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { LoaderOrder } from "@/lib/loader";
import { shortDate } from "@/lib/loader-groups";
import { cn } from "@/lib/utils";
import { bagsWord, itemsSummary, PaymentMark, TransportNumber } from "./loader-order-card";

/** Крупная строка «70 мешков · 3 500 кг» — главное, что грузчик держит в голове. */
function BagsHeadline({ order }: { order: LoaderOrder }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[56px] font-black leading-none tabular-nums">{order.bags}</span>
        <span className="text-xl font-semibold text-[var(--muted-foreground)]">{bagsWord(order.bags)}</span>
      </div>
      <div className="mt-2 text-2xl font-bold tabular-nums">{Number(order.total_kg)} кг</div>
    </div>
  );
}

/**
 * Экран одного заказа: номер машины, сколько грузить и одна кнопка
 * подтверждения. Свайпа нет — грузчик подтверждает нажатием.
 */
export function LoaderOrderScreen({
  order,
  day,
  today,
  canConfirm,
  busy,
  error,
  number,
  onNumber,
  onBack,
  onConfirm,
  onPrint,
}: {
  order: LoaderOrder;
  day: string;
  today: string;
  /** Без loader.confirm экран остаётся справочным: отгружать нечем. */
  canConfirm: boolean;
  busy: boolean;
  error: string;
  number: string;
  onNumber: (value: string) => void;
  onBack: () => void;
  onConfirm: () => void;
  onPrint: () => void;
}) {
  const overdue = day < today;
  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-lg flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" className="h-11" onClick={onBack}>
          <ArrowLeft className="size-4" /> Назад
        </Button>
        <span
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs font-bold uppercase tracking-wide",
            overdue ? "bg-[var(--destructive)] text-white" : "bg-[var(--foreground)] text-[var(--background)]",
          )}
        >
          {overdue ? "просрочено" : day === today ? "сегодня" : "план"} · {shortDate(day)}
        </span>
      </div>

      <div className="flex flex-col gap-5 rounded-2xl border-2 border-[var(--border)] bg-[var(--card)] p-5">
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            {order.transport_type === "train" ? "Вагон" : "Машина"}
          </span>
          {/* Номер вводит оператор перед отгрузкой: клиент его часто не указывает. */}
          <Input
            aria-label={order.transport_type === "train" ? "Номер вагона" : "Номер машины"}
            placeholder={order.transport_type === "train" ? "8 цифр" : "403 BJN 13"}
            autoCapitalize="characters"
            className="h-14 text-2xl font-bold tracking-wide"
            value={number}
            onChange={(event) => onNumber(event.target.value.toUpperCase())}
          />
        </div>
        <div className="border-t pt-4">
          <BagsHeadline order={order} />
          <div className="mt-3 text-base font-semibold">{itemsSummary(order)}</div>
          <div className="mt-1 text-sm text-[var(--muted-foreground)]">
            №{order.id} · {order.client_name}
          </div>
          {/* Оплату показываем, но отгрузку не блокируем: возят и в долг. */}
          <PaymentMark order={order} className="mt-3" />
        </div>
        {order.items.length > 1 && (
          <ul className="flex flex-col gap-1 border-t pt-3 text-sm">
            {order.items.map((item, index) => (
              <li key={index} className="flex justify-between gap-3">
                <span className="min-w-0 truncate">{item.label}</span>
                <span className="shrink-0 font-semibold tabular-nums">
                  {item.quantity} {bagsWord(item.quantity)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error && (
        <p role="alert" className="rounded-xl bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}

      <div className="mt-auto flex flex-col gap-2">
        {canConfirm && (
          <Button className="h-16 w-full text-lg" disabled={busy || !number.trim()} onClick={onConfirm}>
            <PackageCheck className="size-6" />
            {busy ? "Отгружаем…" : "Подтвердить отгрузку"}
          </Button>
        )}
        <Button variant="ghost" className="h-11 w-full" onClick={onPrint}>
          <Printer className="size-4" /> Накладная
        </Button>
      </div>
    </div>
  );
}

/** Экран после отгрузки: подтверждение крупно, накладная и возврат к списку. */
export function LoaderShippedScreen({
  order,
  busy,
  onPrint,
  onBack,
  onUndo,
}: {
  order: LoaderOrder;
  busy: boolean;
  onPrint: () => void;
  onBack: () => void;
  /** Нажал не тот заказ — отмена сразу здесь, пока грузчик у экрана. */
  onUndo: () => void;
}) {
  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-lg flex-col items-center gap-6 rounded-2xl bg-[var(--success)] p-6 text-center text-white">
      <div className="flex size-24 items-center justify-center rounded-full bg-white/15">
        <PackageCheck className="size-14" />
      </div>
      <div>
        <div className="text-2xl font-black leading-tight">Отгрузка подтверждена</div>
        <div className="mt-4 inline-block">
          <TransportNumber order={order} size="lg" />
        </div>
        <div className="mt-4 text-lg font-bold tabular-nums">
          {order.bags} {bagsWord(order.bags)} · {Number(order.total_kg)} кг
        </div>
        <div className="mt-1 text-sm opacity-90">
          №{order.id} · {order.client_name}
        </div>
      </div>
      <div className="mt-auto flex w-full flex-col gap-2">
        <Button className="h-14 w-full bg-white text-base text-[var(--success)] hover:bg-white/90" onClick={onPrint}>
          <Printer className="size-5" /> Печать накладной
        </Button>
        <Button
          variant="outline"
          className="h-14 w-full border-white/60 bg-transparent text-base text-white hover:bg-white/10"
          onClick={onBack}
        >
          К списку заказов
        </Button>
        {order.can_rollback !== false && (
          <Button variant="ghost" className="h-12 w-full text-white hover:bg-white/10" disabled={busy} onClick={onUndo}>
            <Undo2 className="size-4" /> {busy ? "Отменяем…" : "Отменить отгрузку"}
          </Button>
        )}
      </div>
    </div>
  );
}
