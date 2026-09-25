"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { can } from "@/lib/can";
import { MANUAL_REASON_MAX_LENGTH, formatKg, isManualReasonValid, passageNetKg } from "@/lib/grain";
import type { GrainWagon } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { useWeighingDialog } from "./use-weighing-dialog";

export function ExitWeightCorrectionDialog({
  wagon,
  onChanged,
  onBusyChange,
  disabled,
}: {
  wagon: GrainWagon;
  onChanged: () => void;
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
}) {
  const { me } = useAuth();
  const dialog = useWeighingDialog(onBusyChange);
  const [weight, setWeight] = useState("");
  const [expectedWeight, setExpectedWeight] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const allowed = can(me, "grain.correct_weighing");
  const kg = Number(weight);
  const valid =
    /^\d+$/.test(weight) &&
    Number.isSafeInteger(kg) &&
    kg > (wagon.entry_weight_kg ?? 0) &&
    kg !== expectedWeight &&
    isManualReasonValid(reason);
  function save() {
    if (!valid) return;
    void dialog.submit(
      () =>
        api.post(`/grain/passages/${wagon.id}/correct-exit-weight/`, {
          exit_weight_kg: kg,
          expected_exit_weight_kg: expectedWeight,
          reason: reason.trim(),
        }),
      onChanged,
    );
  }
  if (
    !allowed ||
    wagon.direction !== "passage" ||
    !["at_silo", "completed"].includes(wagon.status) ||
    wagon.entry_weight_kg == null
  )
    return null;
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => {
          setExpectedWeight(wagon.exit_weight_kg ?? null);
          setWeight(String(wagon.exit_weight_kg ?? ""));
          setReason("");
          dialog.show();
        }}
      >
        {wagon.exit_weight_kg == null ? "Внести выездной вес вручную" : "Изменить выездной вес"}
      </Button>
      <ConfirmDialog
        open={dialog.open}
        title="Выездной вес"
        description={`Машина ${wagon.number}. Начальный вес: ${formatKg(wagon.entry_weight_kg)}.`}
        confirmLabel={expectedWeight == null ? "Записать вес и завершить рейс" : "Сохранить исправление"}
        confirmVariant="default"
        busy={dialog.busy}
        error={dialog.error}
        confirmDisabled={!valid}
        onConfirm={save}
        onClose={dialog.close}
      >
        <p className="text-sm">Текущий выездной вес: {formatKg(expectedWeight)}</p>
        <div>
          <Label htmlFor="correct-exit-weight">Выездной вес, кг</Label>
          <Input
            id="correct-exit-weight"
            value={weight}
            inputMode="numeric"
            disabled={dialog.busy}
            onChange={(e) => setWeight(e.target.value)}
          />
        </div>
        {valid && (
          <p className="font-semibold">Нетто после исправления: {formatKg(passageNetKg(kg, wagon.entry_weight_kg))}</p>
        )}
        <div>
          <Label htmlFor="correct-exit-reason">Причина исправления веса</Label>
          <Input
            id="correct-exit-reason"
            value={reason}
            maxLength={MANUAL_REASON_MAX_LENGTH}
            disabled={dialog.busy}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          Прежний вес и фото останутся в истории. Исправление сохранится с вашим именем и причиной.
        </p>
      </ConfirmDialog>
    </>
  );
}
