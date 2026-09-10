"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg } from "@/lib/grain";
import { formatDateTime } from "@/lib/utils";
import type { GrainUnassignedWeighing } from "@/lib/types";
import { useAuth } from "@/store/auth";

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
  const [open, setOpen] = useState(false);
  const [number, setNumber] = useState("");
  const [cargo, setCargo] = useState("Отруби");
  const [weight, setWeight] = useState("");
  const [arrivedAt, setArrivedAt] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
    reason.trim().length >= 5,
  );

  function close() {
    if (busy) return;
    setOpen(false);
    onBusyChange?.(false);
  }
  async function save() {
    if (!allowed || !valid || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/grain/passages/manual-entry/", {
        number: number.trim(),
        cargo_name: cargo.trim(),
        entry_weight_kg: entryKg,
        arrived_at: new Date(arrivedAt).toISOString(),
        reason: reason.trim(),
        ...(item ? { unassigned_weighing: item.id } : {}),
      });
      setOpen(false);
      onBusyChange?.(false);
      onChanged();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
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
          setCargo("Отруби");
          setWeight("");
          setArrivedAt("");
          setReason("");
          setError("");
          setOpen(true);
          onBusyChange?.(true);
        }}
      >
        {item ? "Указать начальный вес" : "Заезд вручную"}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={close}
        title="Заезд без фото"
        description={
          item
            ? "Укажите фактические данные пропущенного заезда. Сохранённый выезд завершит этот рейс."
            : "Укажите фактический вес пустой машины и время заезда. Машина появится на территории."
        }
        confirmLabel={item ? "Создать и завершить рейс" : "Создать заезд"}
        confirmVariant="default"
        busy={busy}
        error={error}
        confirmDisabled={!allowed || !valid}
        onConfirm={() => void save()}
      >
        <div>
          <Label htmlFor="manual-entry-number">Номер машины</Label>
          <Input
            id="manual-entry-number"
            value={number}
            disabled={busy}
            maxLength={30}
            onChange={(e) => setNumber(e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <Label htmlFor="manual-entry-cargo">Груз на вывоз</Label>
          <Input
            id="manual-entry-cargo"
            value={cargo}
            disabled={busy}
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
            disabled={busy}
            onChange={(e) => setWeight(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="manual-entry-time">Фактическое время заезда</Label>
          <Input
            id="manual-entry-time"
            type="datetime-local"
            value={arrivedAt}
            disabled={busy}
            onChange={(e) => setArrivedAt(e.target.value)}
          />
        </div>
        {item && (
          <p className="rounded-lg border p-3 text-sm">
            Сохранённый выезд: {formatKg(item.weight_kg)} · {formatDateTime(item.stable_weight_at)}
            {valid && <span className="mt-1 block font-semibold">Нетто: {formatKg(item.weight_kg - entryKg)}</span>}
          </p>
        )}
        <div>
          <Label htmlFor="manual-entry-reason">Причина ручного ввода</Label>
          <Input
            id="manual-entry-reason"
            value={reason}
            disabled={busy}
            maxLength={300}
            placeholder="Например: заезд пропущен, вес из журнала весов"
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          Фото заезда не требуется. Автор, время внесения и причина сохранятся в истории рейса.
        </p>
      </ConfirmDialog>
    </>
  );
}
