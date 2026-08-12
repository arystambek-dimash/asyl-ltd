"use client";

import { useEffect, useId, useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiError } from "@/lib/api";
import { formatKg, isFinishedGrainWagon } from "@/lib/grain";
import type { GrainWagon } from "@/lib/types";

type DeleteResult = { reverted_kg?: number };
type DeletePayload = {
  reason: string;
  confirm_unrecorded_grain_handled?: true;
};

const UNRECORDED_GRAIN_CONFIRMATION_STATUSES = new Set(["unloading", "unloading_completed"]);

export function GrainWagonDeleteDialog({
  wagon,
  open,
  onClose,
  onDeleted,
}: {
  wagon: GrainWagon | null;
  open: boolean;
  onClose: () => void;
  onDeleted: (result: DeleteResult) => void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [unrecordedGrainHandled, setUnrecordedGrainHandled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setReason("");
    setUnrecordedGrainHandled(false);
    setError("");
  }, [open, wagon?.id]);

  if (!wagon) return null;

  const wagonId = wagon.id;
  const finished = isFinishedGrainWagon(wagon.status);
  const needsUnrecordedGrainConfirmation =
    wagon.direction === "intake" && UNRECORDED_GRAIN_CONFIRMATION_STATUSES.has(wagon.status);
  const trimmedReason = reason.trim();
  const validReason = trimmedReason.length >= 5;
  const description = finished
    ? wagon.direction === "passage"
      ? "Запись о вывозе будет удалена без возможности восстановления. Остатки склада не изменятся."
      : `Запись будет удалена без возможности восстановления.${
          wagon.net_weight_kg != null
            ? ` Принятые ${formatKg(wagon.net_weight_kg)} вернутся из силоса, чтобы остаток сошёлся.`
            : ""
        }`
    : "Рейс сейчас числится на территории. Удаляйте его только если техника фактически уехала или запись создана ошибочно.";

  async function confirmDelete() {
    if (!validReason || (needsUnrecordedGrainConfirmation && !unrecordedGrainHandled) || busy) return;
    setBusy(true);
    setError("");
    try {
      const payload: DeletePayload = { reason: trimmedReason };
      if (needsUnrecordedGrainConfirmation) payload.confirm_unrecorded_grain_handled = true;
      const { data } = await api.delete<DeleteResult>(`/grain/wagons/${wagonId}/delete/`, {
        data: payload,
      });
      onClose();
      onDeleted(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title={`Удалить рейс ${wagon.number || `#${wagon.id}`}?`}
      description={description}
      confirmLabel={finished ? "Удалить рейс" : "Удалить активный рейс"}
      busy={busy}
      error={error}
      confirmDisabled={!validReason || (needsUnrecordedGrainConfirmation && !unrecordedGrainHandled)}
      onConfirm={() => void confirmDelete()}
    >
      {!finished && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-900">
          Внимание: это активный рейс. Операция необратима.
        </p>
      )}
      <div>
        <Label htmlFor={reasonId}>Причина удаления *</Label>
        <Input
          id={reasonId}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Например: рейс создан ошибочно"
          autoComplete="off"
          minLength={5}
          maxLength={200}
          required
          disabled={busy}
          data-autofocus
        />
        <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">Минимум 5 символов.</p>
      </div>
      {needsUnrecordedGrainConfirmation && (
        <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-3 text-sm text-amber-950">
          <input
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-amber-700"
            checked={unrecordedGrainHandled}
            onChange={(event) => setUnrecordedGrainHandled(event.target.checked)}
            disabled={busy}
          />
          <span className="font-medium">Подтверждаю: зерно уже учтено отдельно либо фактической разгрузки не было</span>
        </label>
      )}
    </ConfirmDialog>
  );
}
