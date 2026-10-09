"use client";
import { useState } from "react";
import { ArrowLeft, Check, CircleCheck, Minus, PackageCheck, Plus } from "lucide-react";
import { GoodsReturnStatusBadge } from "@/components/orders/goods-return-status";
import { BADGE_TONE_COLOR } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { GOODS_RETURN_STATUS_TONE } from "@/lib/constants";
import type { GoodsReturnItem, GoodsReturnStatus, StorekeeperReturn } from "@/lib/types";
import { acceptedBagsLabel, bagsLabel, bagsWord, cn } from "@/lib/utils";
import { checkedCount, ReturnItemList, ReturnMeta, ReturnSummary } from "./return-card";

/** Цвет исхода — тот же тон, что у бейджа статуса. */
const outcomeColor = (status: GoodsReturnStatus) => BADGE_TONE_COLOR[GOODS_RETURN_STATUS_TONE[status]];

/**
 * Чем кончится «Закрыть возврат» при нынешней приёмке: пока не проверена каждая
 * строка или в строке набрано, но не принято число (`editing`) — закрывать рано;
 * всё принято — полностью, ничего — возврат отменится. Итоги (bags, accepted_bags) — с сервера.
 */
export function closeVerdict(
  row: StorekeeperReturn,
  editing: GoodsReturnItem[] = [],
): { outcome: GoodsReturnStatus | null; text: string } {
  if (editing.length > 0) {
    const labels = editing.map((item) => `«${item.product_label}»`).join(", ");
    return { outcome: null, text: `Примите или отмените набранное число: ${labels}` };
  }
  const checked = checkedCount(row);
  if (checked < row.items.length || row.accepted_bags === null) {
    return { outcome: null, text: `Подтвердите все строки (${checked} из ${row.items.length})` };
  }
  if (row.accepted_bags === 0) return { outcome: "cancelled", text: "Ничего не принято — возврат будет отменён" };
  const accepted = acceptedBagsLabel(row.accepted_bags, row.bags);
  return row.accepted_bags === row.bags
    ? { outcome: "full", text: `${accepted} — полностью` }
    : { outcome: "partial", text: `${accepted} — частично` };
}

/** Целое число без ведущих нулей: «05» → «5», пусто — пусто. */
function digitsOnly(raw: string) {
  const digits = raw.replace(/\D/g, "");
  return digits === "" ? "" : String(Number(digits));
}

/**
 * Строка проверки: мука и сколько мешков привезли. «Подтвердить» — принято всё;
 * «Меньше?» — кладовщик набирает, сколько пришло на самом деле (0…сколько
 * привезли). До закрытия возврата приёмку строки можно поменять. Открыт ли ввод
 * (`editing`), знает страница: пока он открыт, «Закрыть возврат» недоступна.
 */
function ReturnLineCheck({
  item,
  canConfirm,
  busy,
  editing,
  onEditingChange,
  onAccept,
}: {
  item: GoodsReturnItem;
  canConfirm: boolean;
  busy: boolean;
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
  /** true — сервер принял: окно ввода закрывается. */
  onAccept: (bags: number) => Promise<boolean>;
}) {
  const [value, setValue] = useState("");
  const accepted = item.accepted_bags;
  const short = accepted !== null && accepted < item.bags;
  const count = value === "" ? null : Number(value);
  const tooMany = count !== null && count > item.bags;

  function startEdit() {
    setValue(String(accepted ?? item.bags));
    onEditingChange(true);
  }

  async function accept(bags: number) {
    if (await onAccept(bags)) onEditingChange(false);
  }

  return (
    <li
      aria-label={item.product_label}
      className={cn(
        "rounded-xl border-2 p-3",
        accepted === null
          ? "border-[var(--loader-border)]/60!"
          : short
            ? "border-[var(--warning)]!"
            : "border-[var(--success)]!",
      )}
    >
      <div className="flex items-start gap-2.5">
        {accepted !== null && (
          <CircleCheck
            aria-hidden
            className={cn("mt-0.5 size-6 shrink-0", short ? "text-[var(--warning)]" : "text-[var(--success)]")}
          />
        )}
        <span className="min-w-0 flex-1 break-words text-lg font-semibold leading-snug">{item.product_label}</span>
        <span className="shrink-0 text-right leading-none">
          <span className="text-3xl font-black tabular-nums text-[var(--loader-number)]">{item.bags}</span>
          <span className="block text-sm font-semibold text-[var(--muted-foreground)]">{bagsWord(item.bags)}</span>
        </span>
      </div>
      {accepted !== null && (
        <p
          className={cn(
            "mt-2 text-base font-bold tabular-nums",
            short ? "text-[var(--warning)]" : "text-[var(--success)]",
          )}
        >
          принято {accepted} из {item.bags}
        </p>
      )}
      {canConfirm &&
        (editing ? (
          <div className="mt-3 flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor={`accepted-${item.id}`} className="text-sm">
                Сколько мешков пришло? От 0 до {item.bags}
              </Label>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onEditingChange(false)}>
                Отмена
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="size-12 shrink-0"
                aria-label="На мешок меньше"
                disabled={busy || !count}
                onClick={() => setValue(String(Math.max(0, (count ?? 0) - 1)))}
              >
                <Minus />
              </Button>
              <Input
                id={`accepted-${item.id}`}
                inputMode="numeric"
                autoFocus
                value={value}
                onChange={(event) => setValue(digitsOnly(event.target.value))}
                className="h-12 min-w-0 flex-1 text-center text-2xl font-black tabular-nums"
              />
              <Button
                variant="outline"
                className="size-12 shrink-0"
                aria-label="На мешок больше"
                disabled={busy || (count ?? 0) >= item.bags}
                onClick={() => setValue(String(Math.min(item.bags, (count ?? 0) + 1)))}
              >
                <Plus />
              </Button>
            </div>
            {tooMany && (
              <p className="text-sm font-medium text-[var(--destructive)]">
                Привезли {bagsLabel(item.bags)} — больше принять нельзя
              </p>
            )}
            <Button
              className="h-12 w-full text-base"
              disabled={busy || count === null || tooMany}
              onClick={() => count !== null && void accept(count)}
            >
              <Check /> Принять {count === null ? "" : bagsLabel(count)}
            </Button>
          </div>
        ) : (
          <div className={cn("mt-3 flex gap-2", accepted !== null && "justify-end")}>
            {accepted === null && (
              <Button className="h-12 flex-1 text-base" disabled={busy} onClick={() => void accept(item.bags)}>
                <Check /> Подтвердить
              </Button>
            )}
            <Button
              variant={accepted === null ? "outline" : "ghost"}
              className="h-12 text-base"
              disabled={busy}
              onClick={startEdit}
            >
              {short ? "Изменить" : "Меньше?"}
            </Button>
          </div>
        ))}
    </li>
  );
}

/**
 * Экран приёмки одного возврата: кто привёз, сколько всего и проверка по
 * списку — строка за строкой. Денег кладовщик не видит. Без storekeeper.confirm
 * экран справочный.
 */
export function StorekeeperReturnScreen({
  row,
  canConfirm,
  busy,
  error,
  editingIds,
  onEditingChange,
  onBack,
  onAccept,
}: {
  row: StorekeeperReturn;
  canConfirm: boolean;
  busy: boolean;
  error: string;
  /** Строки, где открыт ввод «сколько пришло». */
  editingIds: ReadonlySet<number>;
  onEditingChange: (item: GoodsReturnItem, editing: boolean) => void;
  onBack: () => void;
  onAccept: (item: GoodsReturnItem, bags: number) => Promise<boolean>;
}) {
  return (
    <div className="mx-auto flex w-full max-w-lg flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" className="h-11" disabled={busy} onClick={onBack}>
          <ArrowLeft className="size-4" /> Назад
        </Button>
        <GoodsReturnStatusBadge row={row} className="h-7 px-3 text-sm" />
      </div>

      <div className="flex flex-col gap-5 rounded-2xl border-2 border-[var(--loader-border)]! bg-[var(--card)] p-5">
        <div>
          <h2 className="break-words text-2xl font-black leading-tight">{row.client_name}</h2>
          <ReturnMeta row={row} className="mt-1" />
        </div>
        <div className="border-t-2 border-[var(--loader-border)]/50! pt-4">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <span className="text-[56px] font-black leading-none tabular-nums text-[var(--loader-number)]">
              {row.bags}
            </span>
            <span className="text-xl font-semibold text-[var(--muted-foreground)]">{bagsWord(row.bags)} привезли</span>
          </div>
          <p className="mt-2 text-base font-semibold tabular-nums">
            Проверено {checkedCount(row)} из {row.items.length}
          </p>
        </div>
        <ul aria-label="Проверка по списку" className="flex flex-col gap-3">
          {row.items.map((item) => (
            <ReturnLineCheck
              key={item.id}
              item={item}
              canConfirm={canConfirm}
              busy={busy}
              editing={editingIds.has(item.id)}
              onEditingChange={(editing) => onEditingChange(item, editing)}
              onAccept={(bags) => onAccept(item, bags)}
            />
          ))}
        </ul>
      </div>

      <FormError message={error} className="rounded-xl px-4 py-3" />
    </div>
  );
}

/**
 * Нижняя панель экрана приёмки: чем кончится закрытие и большая кнопка «Закрыть
 * возврат». `editing` — строки с набранным, но не принятым числом: закрыть
 * сейчас значило бы провести старое число.
 */
export function CloseReturnBar({
  row,
  editing,
  busy,
  onClose,
}: {
  row: StorekeeperReturn;
  editing: GoodsReturnItem[];
  busy: boolean;
  onClose: () => void;
}) {
  const verdict = closeVerdict(row, editing);
  return (
    <div className="border-t-2 border-[var(--loader-border)]/50! bg-[var(--card)] px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <div className="mx-auto flex w-full max-w-lg flex-col gap-2">
        <p
          role="status"
          className="text-center text-base font-semibold tabular-nums"
          style={verdict.outcome ? { color: outcomeColor(verdict.outcome) } : undefined}
        >
          {verdict.text}
        </p>
        <Button className="h-16 w-full text-lg" disabled={busy || verdict.outcome === null} onClick={onClose}>
          <PackageCheck className="size-6!" /> Закрыть возврат
        </Button>
      </div>
    </div>
  );
}

/** Возврат закрыт: статус крупно, принято из привезённого и назад к списку. */
export function StorekeeperClosedScreen({ row, onBack }: { row: StorekeeperReturn; onBack: () => void }) {
  const color = outcomeColor(row.status);
  return (
    <div
      className="mx-auto flex w-full max-w-lg flex-col items-center gap-5 rounded-2xl border-2 bg-[var(--card)] p-6 text-center"
      style={{ borderColor: color }}
    >
      <div
        className="flex size-20 items-center justify-center rounded-full border-4"
        style={{ borderColor: color, color }}
      >
        <PackageCheck className="size-11" aria-hidden />
      </div>
      <div role="status">
        <div className="text-sm font-medium text-[var(--muted-foreground)]">Возврат №{row.id} закрыт</div>
        <div className="mt-1 text-3xl font-black leading-tight" style={{ color }}>
          {row.status_label}
        </div>
        <div className="mt-3 text-xl font-bold tabular-nums">
          <ReturnSummary row={row} />
        </div>
        <div className="mt-2 text-sm text-[var(--muted-foreground)]">
          {row.client_name} · {row.warehouse_name}
        </div>
      </div>
      <ReturnItemList row={row} className="w-full text-left" />
      <Button className="h-14 w-full text-base" onClick={onBack}>
        К списку возвратов
      </Button>
    </div>
  );
}
