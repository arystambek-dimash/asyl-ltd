"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { can } from "@/lib/can";
import {
  DEFAULT_PASSAGE_CARGO,
  MANUAL_REASON_MAX_LENGTH,
  formatKg,
  isManualReasonValid,
  passageNetKg,
} from "@/lib/grain";
import { formatDateTime } from "@/lib/utils";
import type { GrainUnassignedWeighing } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { useWeighingDialog } from "./use-weighing-dialog";

export function ManualPassageEntryDialog({
  item,
  onChanged,
  onBusyChange,
  disabled,
}: {
  item?: GrainUnassignedWeighing;
  onChanged: () => void;
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
}) {
  const { me } = useAuth();
  const dialog = useWeighingDialog(onBusyChange);
  const [number, setNumber] = useState("");
  const [cargo, setCargo] = useState(DEFAULT_PASSAGE_CARGO);
  const [weight, setWeight] = useState("");
  const [arrivedAt, setArrivedAt] = useState("");
  const [reason, setReason] = useState("");
  const allowed = can(me, "grain.correct_weighing");
  const entryKg = Number(weight);
  const entryAt = new Date(arrivedAt).getTime();
  const valid = Boolean(
    number.trim() &&
    cargo.trim() &&
    /^\d+$/.test(weight) &&
    entryKg > 0 &&
    Number.isSafeInteger(entryKg) &&
    Number.isFinite(entryAt) &&
    entryAt <= Date.now() &&
    (!item || (entryKg < item.weight_kg && entryAt < new Date(item.stable_weight_at).getTime())) &&
    isManualReasonValid(reason),
  );

  function save() {
    if (!valid) return;
    void dialog.submit(
      () =>
        api.post("/grain/passages/manual-entry/", {
          number: number.trim(),
          cargo_name: cargo.trim(),
          entry_weight_kg: entryKg,
          arrived_at: new Date(arrivedAt).toISOString(),
          reason: reason.trim(),
          ...(item ? { unassigned_weighing: item.id } : {}),
        }),
      onChanged,
    );
  }
  if (!allowed) return null;
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => {
          setNumber(item?.vehicle_number || "");
          setCargo(DEFAULT_PASSAGE_CARGO);
          setWeight("");
          setArrivedAt("");
          setReason("");
          dialog.show();
        }}
      >
        {item ? "Указать начальный вес" : "Заезд вручную"}
      </Button>
      <ConfirmDialog
        open={dialog.open}
        onClose={dialog.close}
        title="Заезд без фото"
        description={
          item
            ? "Укажите фактические данные пропущенного заезда. Сохранённый выезд завершит этот рейс."
            : "Укажите фактический вес пустой машины и время заезда. Машина появится на территории."
        }
        confirmLabel={item ? "Создать и завершить рейс" : "Создать заезд"}
        confirmVariant="default"
        busy={dialog.busy}
        error={dialog.error}
        confirmDisabled={!valid}
        onConfirm={save}
      >
        <div>
          <Label htmlFor="manual-entry-number">Номер машины</Label>
          <Input
            id="manual-entry-number"
            value={number}
            disabled={dialog.busy}
            maxLength={30}
            onChange={(e) => setNumber(e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <Label htmlFor="manual-entry-cargo">Груз на вывоз</Label>
          <Input
            id="manual-entry-cargo"
            value={cargo}
            disabled={dialog.busy}
            maxLength={100}
            onChange={(e) => setCargo(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="manual-entry-weight">Начальный вес пустой машины, кг</Label>
          <Input
            id="manual-entry-weight"
            inputMode="numeric"
            value={weight}
            disabled={dialog.busy}
            onChange={(e) => setWeight(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="manual-entry-time">Фактическое время заезда</Label>
          <Input
            id="manual-entry-time"
            type="datetime-local"
            value={arrivedAt}
            disabled={dialog.busy}
            onChange={(e) => setArrivedAt(e.target.value)}
          />
        </div>
        {item && (
          <p className="rounded-lg border p-3 text-sm">
            Сохранённый выезд: {formatKg(item.weight_kg)} · {formatDateTime(item.stable_weight_at)}
            {valid && (
              <span className="mt-1 block font-semibold">Нетто: {formatKg(passageNetKg(item.weight_kg, entryKg))}</span>
            )}
          </p>
        )}
        <div>
          <Label htmlFor="manual-entry-reason">Причина ручного ввода</Label>
          <Input
            id="manual-entry-reason"
            value={reason}
            disabled={dialog.busy}
            maxLength={MANUAL_REASON_MAX_LENGTH}
            placeholder="Например: заезд пропущен, вес из журнала весов"
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          Фото заезда не требуется. Тара сохранится по номеру для следующих выездов с пометкой «введена вручную». Автор,
          время внесения и причина останутся в истории рейса.
        </p>
      </ConfirmDialog>
    </>
  );
}
