import { Camera, PackagePlus, Scale, TrainFront, Truck, Warehouse, type LucideIcon } from "lucide-react";
import type { BadgeTone } from "@/lib/constants";
import { formatMoney } from "@/lib/utils";
import type { GrainSilo, GrainWagon } from "@/lib/types";

/** Груз вывоза по умолчанию — как VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME на бэке. */
export const DEFAULT_PASSAGE_CARGO = "Отруби";

/** Цвет нового типа зерна — как default у SiloType.color на бэке. */
export const DEFAULT_GRAIN_TYPE_COLOR = "#C58A35";

/** Причина ручного взвешивания: те же границы, что у сериализаторов бэка. */
export const MANUAL_REASON_MAX_LENGTH = 300;
const MANUAL_REASON_MIN_LENGTH = 5;

export function isManualReasonValid(reason: string): boolean {
  return reason.trim().length >= MANUAL_REASON_MIN_LENGTH;
}

/**
 * Нетто вывоза для превью ещё не сохранённого ввода: машина заезжает пустой,
 * значит выезд минус въезд. Сохранённое нетто считает бэкенд
 * (Wagon.computed_net_kg, у прихода формула обратная).
 */
export function passageNetKg(exitKg: number, entryKg: number): number {
  return exitKg - entryKg;
}

/** Силос без типа принимает любое зерно, с типом — только своё. */
export function siloAcceptsType(silo: Pick<GrainSilo, "silo_type">, typeId: number): boolean {
  return silo.silo_type == null || silo.silo_type === typeId;
}

export function grainWorkspaceHref(direction: GrainWagon["direction"]): string {
  return direction === "passage" ? "/grain/passages" : "/grain";
}

/** Страница рейса: приход и вывоз живут под разными маршрутами. */
export function grainTripHref(trip: Pick<GrainWagon, "id" | "direction">): string {
  return `/grain/${trip.direction === "passage" ? "passages" : "wagons"}/${trip.id}`;
}

/** Накладная на отпуск печатается только по вывозу. */
export function passageWaybillHref(id: number): string {
  return `/grain/passages/${id}/waybill`;
}

const FINISHED_WAGON_STATUSES = new Set(["completed", "cancelled", "return_to_supplier", "exited"]);

/** Used only to explain the consequence in the UI; deletion eligibility is
 * always decided by the backend. */
export function isFinishedGrainWagon(status: string): boolean {
  return FINISHED_WAGON_STATUSES.has(status);
}

/** Expected records are managed through their supply, while unplanned OCR
 * records must first be approved or cancelled. The backend remains the final
 * authority for every other status. */
export function isGrainWagonDeleteSupported(status: string): boolean {
  return status !== "expected" && status !== "unplanned";
}

/** Тона статусов вагона; подписи приходят с бэка (status_label). */
export const GRAIN_STATUS_TONE: Record<string, BadgeTone> = {
  expected: "muted",
  arrived: "primary",
  at_silo: "warning",
  gross_weighed: "primary",
  lab_pending: "warning",
  unloading_allowed: "success",
  silo_assigned: "primary",
  unloading: "warning",
  unloading_completed: "primary",
  tare_weighed: "primary",
  inventoried: "success",
  exit_allowed: "success",
  exited: "muted",
  completed: "muted",
  unplanned: "warning",
  waiting_for_approval: "warning",
  rejected: "destructive",
  quarantine: "destructive",
  insufficient_capacity: "destructive",
  weight_discrepancy: "destructive",
  reweighing_required: "warning",
  blocked: "destructive",
  return_to_supplier: "destructive",
  cancelled: "muted",
};

type GrainTripStep = { label: string; icon: LucideIcon };

/** Маршрут поезда в 4 шага. */
const INTAKE_TRIP_STEPS: readonly GrainTripStep[] = [
  { label: "Заезд", icon: Camera },
  { label: "Входные весы (позже)", icon: Scale },
  { label: "Разгрузка", icon: Warehouse },
  { label: "Выходные весы (позже)", icon: TrainFront },
];

/** Вывоз: машина заезжает пустой, грузится и уезжает — разгрузки нет. */
const PASSAGE_TRIP_STEPS: readonly GrainTripStep[] = [
  { label: "Заезд", icon: Camera },
  { label: "Весы пустого", icon: Scale },
  { label: "Погрузка", icon: PackagePlus },
  { label: "Весы гружёного", icon: Truck },
];

export function grainTripSteps(direction: GrainWagon["direction"]): readonly GrainTripStep[] {
  return direction === "passage" ? PASSAGE_TRIP_STEPS : INTAKE_TRIP_STEPS;
}

const TRIP_STEP_BY_STATUS: Record<string, number> = {
  expected: 0,
  waiting_for_approval: 0,
  unplanned: 0,
  arrived: 1,
  gross_weighed: 2,
  lab_pending: 2,
  unloading_allowed: 2,
  silo_assigned: 2,
  at_silo: 2,
  unloading: 2,
  quarantine: 2,
  insufficient_capacity: 2,
  blocked: 2,
  rejected: 2,
  unloading_completed: 3,
  reweighing_required: 3,
  tare_weighed: 3,
  weight_discrepancy: 3,
  inventoried: 3,
  exit_allowed: 3,
  return_to_supplier: 3,
  exited: 4,
  completed: 4,
  cancelled: 4,
};

/**
 * Шаг рейса по статусу — один для таблицы и карточки рейса; 4 — рейс
 * завершён. Неизвестные статусы легаси-потока прижимаются к силосному этапу.
 */
export function grainTripStepIndex(status: string): number {
  return TRIP_STEP_BY_STATUS[status] ?? 2;
}

/** Вес храним в кг; крупные значения удобнее читать в тоннах. */
export function formatKg(value: number | null | undefined): string {
  if (value == null) return "—";
  if (Math.abs(value) >= 100_000) {
    const tons = value / 1000;
    return `${formatMoney(String(Math.round(tons * 10) / 10))} т`;
  }
  return `${formatMoney(value)} кг`;
}

export const GRAIN_MOVEMENT_LABELS: Record<string, string> = {
  income: "Приход",
  expense: "Расход",
  transfer_in: "Перемещение (в)",
  transfer_out: "Перемещение (из)",
  adjustment: "Корректировка",
  inventory_correction: "Инвентаризация",
};

/** Ручные операции с остатком силоса: приход идёт только через вагоны. */
export const SILO_ADJUST_MOVEMENTS = [
  "adjustment",
  "inventory_correction",
  "expense",
  "transfer_in",
  "transfer_out",
] as const;

/** Подпись силоса в списке выбора: «Силос 1 · свободно 120 т». */
export function siloOptionLabel(silo: Pick<GrainSilo, "name" | "free_capacity_kg">): string {
  return `${silo.name} · свободно ${formatKg(silo.free_capacity_kg)}`;
}

/** Вывоз, у которого камера не смогла прочитать номер: оператор допишет его позже. */
export function isPassagePlateMissing(wagon: Pick<GrainWagon, "direction" | "number">): boolean {
  return wagon.direction === "passage" && !wagon.number.trim();
}
