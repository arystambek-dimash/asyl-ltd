import { fireEvent, screen } from "@testing-library/react";
import type { GrainWagon } from "@/lib/types";

/** Вагон прихода без номера и взвешиваний; в тесте переопределяются только важные ему поля. */
export function makeGrainWagon(overrides: Partial<GrainWagon> = {}): GrainWagon {
  return {
    id: 1,
    supply: null,
    number: "",
    number_source: "manual",
    workflow: "simple",
    direction: "intake",
    cargo_name: "",
    status: "arrived",
    status_label: "Прибыл",
    unplanned: false,
    supplier: "",
    culture: "",
    grain_class: "",
    grain_type: null,
    grain_type_name: "",
    document_weight_kg: null,
    expected_weight_kg: null,
    arrived_at: null,
    gross_weight_kg: null,
    tare_weight_kg: null,
    net_weight_kg: null,
    entry_weight_kg: null,
    exit_weight_kg: null,
    weight_difference_kg: null,
    weight_difference_percent: null,
    weight_matches: null,
    assigned_silo: null,
    assigned_silo_name: null,
    silo_arrived_at: null,
    exited_at: null,
    created_at: "2026-08-12T00:00:00Z",
    ...overrides,
  };
}

/** Заполняет окно «Заезд без фото»: начальный вес, время заезда, причину и номер, если окно его спрашивает. */
export function fillManualEntry({
  number,
  weight = "4100",
  time = "2026-01-01T10:00",
  reason,
}: {
  number?: string;
  weight?: string;
  time?: string;
  reason: string;
}) {
  const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  if (number !== undefined) fill("Номер машины", number);
  fill("Начальный вес пустой машины, кг", weight);
  fill("Фактическое время заезда", time);
  fill("Причина ручного ввода", reason);
}
