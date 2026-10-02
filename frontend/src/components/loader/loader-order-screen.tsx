"use client";
import { ArrowLeft, ClipboardPaste, Lock, PackageCheck, Printer, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { PlateSuggestions } from "@/components/ui/plate-input";
import { OrderTransportBadge } from "@/components/ui/transport-number";
import { TransportNumberFields } from "@/components/ui/transport-number-fields";
import { WagonList } from "@/components/ui/wagon-list";
import { hasTruckReport, loadWeight, type LoaderOrder } from "@/lib/loader";
import { plannedDayLabel } from "@/lib/loader-groups";
import { transportNumberError, type TransportPair } from "@/lib/plates";
import { bagsLabel, bagsWord, cn, formatIsoDayMonth } from "@/lib/utils";
import { CopyTruckReportButton } from "./copy-truck-report-button";
import { LoaderItemList, PaymentMark } from "./loader-order-card";

/** Крупная строка «70 мешков · 3500 кг» (у вагона — тонны) — главное, что грузчик держит в голове. */
function BagsHeadline({ order }: { order: LoaderOrder }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[56px] font-black leading-none tabular-nums">{order.bags}</span>
        <span className="text-xl font-semibold text-[var(--muted-foreground)]">{bagsWord(order.bags)}</span>
      </div>
      <div className="mt-2 text-2xl font-bold tabular-nums">{loadWeight(order)}</div>
    </div>
  );
}

const FIELD_LABEL = "text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]";

function LockedNumberNote() {
  return (
    <p className="flex items-center gap-1.5 text-sm text-[var(--muted-foreground)]">
      <Lock className="size-4" /> Номер указал клиент — изменить его может только клиент.
    </p>
  );
}

/**
 * Номер транспорта вводит оператор перед отгрузкой — клиент его часто не указывает.
 * Прошлые пары клиента подставляются одним нажатием.
 */
function TransportNumbers({
  order,
  numbers,
  onNumbers,
}: {
  order: LoaderOrder;
  numbers: TransportPair;
  onNumbers: (value: TransportPair) => void;
}) {
  const train = order.transport_type === "train";
  const locked = order.transport_locked;
  const suggestions = train || locked ? [] : order.transport_suggestions;
  const wagonError =
    train && numbers.truck_number !== order.truck_number ? transportNumberError(numbers.truck_number, "train") : null;
  return (
    <div className="flex flex-col gap-3">
      <TransportNumberFields
        id="loader"
        transportType={order.transport_type}
        size="lg"
        labelClassName={FIELD_LABEL}
        defaultCountry={order.client_country}
        value={numbers}
        onChange={onNumbers}
        errors={{ truck: wagonError }}
        disabled={locked}
      />
      {locked && <LockedNumberNote />}
      <PlateSuggestions suggestions={suggestions} current={numbers} onPick={onNumbers} />
    </div>
  );
}

/**
 * Экран одного заказа: номер машины, сколько грузить и одна кнопка
 * подтверждения. Свайпа нет — грузчик подтверждает нажатием.
 */
export function LoaderOrderScreen({
  order,
  today,
  canConfirm,
  busy,
  error,
  numbers,
  onNumbers,
  onBack,
  onConfirm,
  onPrint,
  onShipByReport,
  busyLabel = "Отгружаем…",
}: {
  order: LoaderOrder;
  today: string;
  /** Без loader.confirm экран остаётся справочным: отгружать нечем. */
  canConfirm: boolean;
  busy: boolean;
  error: string;
  numbers: TransportPair;
  onNumbers: (value: TransportPair) => void;
  onBack: () => void;
  onConfirm: () => void;
  onPrint: () => void;
  /** Вагонный заказ целой партией: вагоны, станция и день — из отчёта о вагонах. */
  onShipByReport: () => void;
  /** Надпись кнопки, пока идёт работа: до опросника складов — «Проверяем склады…». */
  busyLabel?: string;
}) {
  const day = order.planned_on;
  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-lg flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        {/* Пока идёт проверка складов или отгрузка, с заказа не уйти: ответ сервера — про этот заказ. */}
        <Button variant="outline" className="h-11" disabled={busy} onClick={onBack}>
          <ArrowLeft className="size-4" /> Назад
        </Button>
        <span
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs font-bold uppercase tracking-wide",
            day < today ? "bg-[var(--destructive)] text-white" : "bg-[var(--foreground)] text-[var(--background)]",
          )}
        >
          {plannedDayLabel(day, today) || "план"} · {formatIsoDayMonth(day)}
        </span>
      </div>

      <div className="flex flex-col gap-5 rounded-2xl border-2 border-[var(--border)] bg-[var(--card)] p-5">
        {/* Кому грузим — первым и крупно: так грузчик сверяет заказ с машиной. */}
        <div>
          <h2 className="break-words text-2xl font-black leading-tight">{order.client_name}</h2>
          <div className="mt-1 text-sm font-medium tabular-nums text-[var(--muted-foreground)]">№{order.id}</div>
        </div>
        <TransportNumbers order={order} numbers={numbers} onNumbers={onNumbers} />
        <div className="border-t pt-4">
          <BagsHeadline order={order} />
          <LoaderItemList items={order.items} className="mt-4" />
          {/* Оплату показываем, но отгрузку не блокируем: возят и в долг. */}
          <PaymentMark order={order} className="mt-4" />
        </div>
      </div>

      <FormError message={error} className="rounded-xl px-4 py-3" />

      <div className="mt-auto flex flex-col gap-2">
        {canConfirm && (
          <Button className="h-16 w-full text-lg" disabled={busy || !numbers.truck_number.trim()} onClick={onConfirm}>
            <PackageCheck className="size-6" />
            {busy ? busyLabel : "Подтвердить отгрузку"}
          </Button>
        )}
        {canConfirm && order.transport_type === "train" && (
          <Button variant="outline" className="h-14 w-full text-base" disabled={busy} onClick={onShipByReport}>
            <ClipboardPaste className="size-5" /> Отгрузить по отчёту
          </Button>
        )}
        <Button variant="ghost" className="h-11 w-full" onClick={onPrint}>
          <Printer className="size-4" /> Накладная
        </Button>
      </div>
    </div>
  );
}

/** Экран после отгрузки: подтверждение крупно, накладная, отчёт фуры для чата и возврат к списку. */
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
          <OrderTransportBadge order={order} size="lg" />
        </div>
        <div className="mt-4 text-lg font-bold tabular-nums">
          {bagsLabel(order.bags)} · {loadWeight(order)}
        </div>
        <WagonList wagons={order.wagons} headline={false} className="mt-3 [&_ul]:justify-center" />
        <div className="mt-1 text-sm opacity-90">
          №{order.id} · {order.client_name}
        </div>
      </div>
      <div className="mt-auto flex w-full flex-col gap-2">
        <Button className="h-14 w-full bg-white text-base text-[var(--success)] hover:bg-white/90" onClick={onPrint}>
          <Printer className="size-5" /> Печать накладной
        </Button>
        {/* Отгрузку фуры — сразу в чат отгрузок: текст в буфер, грузчик вставляет в WhatsApp. */}
        {hasTruckReport(order) && (
          <CopyTruckReportButton
            scope={{ order: order.id }}
            buttonClassName="h-14 border-white/60 bg-transparent text-base text-white hover:bg-white/10 hover:text-white"
          />
        )}
        <Button
          variant="outline"
          className="h-14 w-full border-white/60 bg-transparent text-base text-white hover:bg-white/10"
          onClick={onBack}
        >
          К списку заказов
        </Button>
        {order.can_rollback && (
          <Button variant="ghost" className="h-12 w-full text-white hover:bg-white/10" disabled={busy} onClick={onUndo}>
            <Undo2 className="size-4" /> {busy ? "Отменяем…" : "Отменить отгрузку"}
          </Button>
        )}
      </div>
    </div>
  );
}
