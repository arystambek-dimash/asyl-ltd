import { formatMoney } from "@/lib/utils";

type Tone = "muted" | "primary" | "success" | "warning" | "destructive";

/** Тона статусов вагона; подписи приходят с бэка (status_label). */
export const GRAIN_STATUS_TONE: Record<string, Tone> = {
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
