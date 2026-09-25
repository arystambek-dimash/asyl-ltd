"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { ArrowRight, Camera, Check, Clock3, TrainFront, Trash2, Truck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { GrainWagonDeleteDialog } from "@/components/grain/wagon-delete-dialog";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { can } from "@/lib/can";
import { groupByDay } from "@/lib/day-groups";
import {
  formatKg,
  grainTripHref,
  grainTripStepIndex,
  grainTripSteps,
  GRAIN_STATUS_TONE,
  isFinishedGrainWagon,
  isPassagePlateMissing,
} from "@/lib/grain";
import type { GrainWagon, Me } from "@/lib/types";
import { useLocalDay } from "@/lib/use-local-day";
import { cn, formatDateTime } from "@/lib/utils";

function WagonStatusBadge({ wagon }: { wagon: Pick<GrainWagon, "status" | "status_label"> }) {
  return (
    <Badge tone={GRAIN_STATUS_TONE[wagon.status] ?? "muted"} dot>
      {wagon.status_label}
    </Badge>
  );
}

const PROBLEM_STATUSES = new Set([
  "weight_discrepancy",
  "reweighing_required",
  "quarantine",
  "rejected",
  "blocked",
  "insufficient_capacity",
  "return_to_supplier",
]);

function wagonCta(wagon: GrainWagon, me: Me | null) {
  const passage = wagon.direction === "passage";
  if (wagon.workflow === "simple") {
    if (wagon.status === "arrived" && can(me, "grain.weigh"))
      return {
        label: passage ? "Взвесить пустую" : "Весы вагонов не подключены",
        variant: passage ? ("default" as const) : ("outline" as const),
      };
    if (wagon.status === "at_silo" && can(me, "grain.weigh"))
      return {
        label: passage ? "Взвесить гружёную" : "Весы вагонов не подключены",
        variant: passage ? ("default" as const) : ("outline" as const),
      };
    if (wagon.status === "weight_discrepancy" && can(me, "grain.inventory"))
      return { label: "Разобрать расхождение", variant: "destructive" as const };
  }
  return { label: "Открыть карточку", variant: "outline" as const };
}

/** Текущий этап словами — вместо графического степпера в тесной ячейке. */
function StageCell({ wagon }: { wagon: GrainWagon }) {
  const steps = grainTripSteps(wagon.direction);
  const index = Math.min(grainTripStepIndex(wagon.status), steps.length);
  const done = index >= steps.length;
  const problem = PROBLEM_STATUSES.has(wagon.status);

  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          "flex size-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold tabular-nums",
          done && "bg-slate-900 text-white",
          !done && problem && "bg-red-100 text-red-700",
          !done && !problem && "bg-amber-100 text-amber-800",
        )}
      >
        {done ? <Check className="size-3.5" /> : index + 1}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-medium">{done ? "Завершён" : steps[index].label}</span>
        <span className="block truncate text-[11px] text-[var(--muted-foreground)]">
          {done ? wagon.status_label : `шаг ${index + 1} из ${steps.length}`}
        </span>
      </span>
    </div>
  );
}

/** Вес с единицами или честная подпись о состоянии источника. */
function WeightCell({ value, pendingLabel }: { value: number | null | undefined; pendingLabel: string }) {
  if (value == null) {
    return <span className="text-[13px] text-[var(--muted-foreground)]">{pendingLabel}</span>;
  }
  return <span className="font-semibold tabular-nums">{formatKg(value)}</span>;
}

const GROUP_META = {
  intake: { title: "Приход", hint: "привозят зерно · заехал гружёным, уехал пустым", Icon: TrainFront },
  passage: { title: "Вывоз", hint: "забирают груз · заехал пустым, уехал гружёным", Icon: Truck },
} as const;

/**
 * День рейса для разбивки таблицы: у завершённого — выезд, у остальных —
 * заезд. Резерв — создание записи, чтобы рейс без отметок не терялся.
 */
function wagonDayDate(wagon: GrainWagon): Date | null {
  const finished = isFinishedGrainWagon(wagon.status);
  const raw = (finished ? wagon.exited_at : null) ?? wagon.arrived_at ?? wagon.created_at ?? null;
  return raw ? new Date(raw) : null;
}

/** Свежие сверху, без даты — в конец: тогда день в таблице не повторяется. */
function sortByDayDesc(wagons: GrainWagon[]): GrainWagon[] {
  const times = new Map(
    wagons.map((wagon) => {
      const time = wagonDayDate(wagon)?.getTime();
      return [wagon, time == null || Number.isNaN(time) ? null : time] as const;
    }),
  );
  return [...wagons].sort((left, right) => {
    const a = times.get(left) ?? null;
    const b = times.get(right) ?? null;
    if (a === b) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return b - a;
  });
}

/**
 * Таблица рейсов одного направления: «Приход» или «Вывоз».
 *
 * Колонки общие, потому что оба сценария — это два взвешивания и итог. Что
 * именно означают цифры, объясняет заголовок группы: у прихода итог — принятое
 * зерно, у вывоза — увезённый груз. Раньше каждая строка была карточкой со
 * степпером, и десяток рейсов не помещался на экран.
 */
export function WagonTable({
  wagons,
  me,
  emptyText,
  direction,
  onDeleted,
}: {
  wagons: GrainWagon[];
  me: Me | null;
  emptyText: string;
  direction: GrainWagon["direction"];
  /** Задан — появляется удаление (дополнительно требуется grain.delete). */
  onDeleted?: () => void;
}) {
  const [pendingDelete, setPendingDelete] = useState<GrainWagon | null>(null);
  const currentDay = useLocalDay();
  const canDelete = Boolean(onDeleted) && can(me, "grain.delete");
  const rows = sortByDayDesc(wagons.filter((wagon) => wagon.direction === direction));

  if (!rows.length) {
    return <FlowEmptyState text={emptyText} direction={direction} />;
  }

  const group = GROUP_META[direction];
  const days = groupByDay(rows, wagonDayDate, currentDay);

  return (
    <div className="overflow-hidden rounded-xl border bg-[var(--card)]">
      <Table>
        <THead>
          <TR className="[&>th]:border-b [&>th]:border-[var(--border)]">
            <TH className="min-w-44">Рейс</TH>
            <TH className="min-w-40">Этап</TH>
            <TH className="min-w-32">Статус</TH>
            <TH className="min-w-28 text-right">Заехал</TH>
            <TH className="min-w-28 text-right">Уехал</TH>
            <TH className="min-w-32 text-right">Итог</TH>
            <TH className="min-w-36">На территории</TH>
            <TH className="w-px" />
          </TR>
        </THead>
        <TBody>
          <tr className="bg-[var(--muted)]/60">
            <td colSpan={8} className="border-b border-[var(--border)] px-3 py-2 sm:px-4">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <group.Icon className="size-3.5 text-[var(--muted-foreground)]" />
                <span className="text-[12px] font-bold uppercase tracking-wide">{group.title}</span>
                <span className="text-[11px] text-[var(--muted-foreground)]">· {group.hint}</span>
                <span className="ml-auto text-[11px] font-semibold tabular-nums text-[var(--muted-foreground)]">
                  {rows.length}
                </span>
              </div>
            </td>
          </tr>
          {days.map((day) => (
            <Fragment key={day.key}>
              <tr className="bg-[var(--muted)]/25">
                <td colSpan={8} className="border-b border-[var(--border)] px-3 py-1.5 sm:px-4">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold">{day.label}</span>
                    <span className="ml-auto text-[11px] tabular-nums text-[var(--muted-foreground)]">
                      {day.items.length}
                    </span>
                  </div>
                </td>
              </tr>
              {day.items.map((wagon) => {
                const cta = wagonCta(wagon, me);
                const finished = isFinishedGrainWagon(wagon.status);
                const passage = wagon.direction === "passage";
                const since = finished
                  ? wagon.exited_at && `выехал ${formatDateTime(wagon.exited_at)}`
                  : wagon.arrived_at && formatDateTime(wagon.arrived_at);
                return (
                  <TR key={wagon.id}>
                    <TD>
                      <Link href={grainTripHref(wagon)} className="block min-w-0 hover:underline">
                        <span className="block truncate font-semibold">
                          {wagon.number || `#${wagon.id}`}
                          {isPassagePlateMissing(wagon) && (
                            <Badge tone="warning" className="ml-2 align-middle">
                              номер не распознан
                            </Badge>
                          )}
                        </span>
                        <span className="block truncate text-[11px] text-[var(--muted-foreground)]">
                          {passage
                            ? wagon.cargo_name || "Вывоз"
                            : [wagon.supplier, wagon.grain_type_name || wagon.culture, wagon.assigned_silo_name]
                                .filter(Boolean)
                                .join(" · ") || "Приход"}
                        </span>
                        {wagon.number_source === "camera" && (
                          <span className="mt-0.5 flex items-center gap-1 text-[11px] text-[var(--muted-foreground)]">
                            <Camera className="size-3 shrink-0" /> Камера {wagon.number_camera_source || "не указана"}
                          </span>
                        )}
                      </Link>
                    </TD>
                    <TD>
                      <StageCell wagon={wagon} />
                    </TD>
                    <TD>
                      <WagonStatusBadge wagon={wagon} />
                    </TD>
                    <TD className="text-right">
                      <WeightCell
                        value={wagon.entry_weight_kg}
                        pendingLabel={passage ? "ждёт весов" : "весы не подключены"}
                      />
                    </TD>
                    <TD className="text-right">
                      <WeightCell
                        value={wagon.exit_weight_kg}
                        pendingLabel={passage ? "ждёт весов" : "весы не подключены"}
                      />
                    </TD>
                    <TD className="text-right">
                      {wagon.net_weight_kg != null ? (
                        <>
                          <span className="block font-bold tabular-nums">{formatKg(wagon.net_weight_kg)}</span>
                          {wagon.weight_difference_kg != null && (
                            <span
                              className={cn(
                                "block text-[11px] tabular-nums",
                                wagon.weight_matches === false
                                  ? "text-[var(--destructive)]"
                                  : "text-[var(--muted-foreground)]",
                              )}
                            >
                              Δ {wagon.weight_difference_kg > 0 ? "+" : ""}
                              {formatKg(wagon.weight_difference_kg)}
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-[13px] text-[var(--muted-foreground)]">
                          {passage ? "после погрузки" : "после разгрузки"}
                        </span>
                      )}
                    </TD>
                    <TD>
                      <span className="text-[12px] text-[var(--muted-foreground)]">{since || "—"}</span>
                    </TD>
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Link
                          href={grainTripHref(wagon)}
                          className={buttonVariants({ size: "sm", variant: cta.variant })}
                        >
                          {cta.label} <ArrowRight className="size-4" />
                        </Link>
                        {canDelete && (
                          <button
                            type="button"
                            aria-label={`Удалить рейс ${wagon.number || `#${wagon.id}`}`}
                            onClick={() => setPendingDelete(wagon)}
                            className="flex size-8 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--destructive)]/10 hover:text-[var(--destructive)]"
                          >
                            <Trash2 className="size-4" />
                          </button>
                        )}
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </Fragment>
          ))}
        </TBody>
      </Table>

      <GrainWagonDeleteDialog
        wagon={pendingDelete}
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onDeleted={() => onDeleted?.()}
      />
    </div>
  );
}

/** Пустое состояние с объяснением маршрута — вместо постоянного баннера. */
export function FlowEmptyState({ text, direction = "intake" }: { text: string; direction?: GrainWagon["direction"] }) {
  const steps = grainTripSteps(direction);

  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
      <Clock3 className="mx-auto size-8 text-slate-300" />
      <p className="mt-3 text-sm font-semibold text-slate-700">{text}</p>
      <div className="mx-auto mt-6 flex max-w-md items-center justify-between gap-1">
        {steps.map((step, index) => {
          const Icon = step.icon;
          return (
            <div key={step.label} className="flex flex-1 items-center">
              {index > 0 && <span className="mx-1 h-px flex-1 bg-slate-200" aria-hidden />}
              <span className="flex flex-col items-center gap-1.5">
                <span className="flex size-9 items-center justify-center rounded-xl bg-white text-slate-500 shadow-sm">
                  <Icon className="size-4" />
                </span>
                <span className="text-[10px] font-semibold text-slate-400">{step.label}</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
