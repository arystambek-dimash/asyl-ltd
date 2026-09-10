"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { api, apiError } from "@/lib/api";
import { apiFileUrl, formatKg } from "@/lib/grain";
import { formatDateTime } from "@/lib/utils";
import type { GrainUnassignedWeighing, GrainWagon, GrainWeighing } from "@/lib/types";

export function HistoricalTareDialog({
  item,
  wagon,
  onChanged,
  onBusyChange,
  disabled,
}: {
  item?: GrainUnassignedWeighing;
  wagon?: GrainWagon;
  onChanged: () => void;
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [resolvedItem, setResolvedItem] = useState(item);
  const [number, setNumber] = useState(item?.vehicle_number || wagon?.number || "");
  const [rows, setRows] = useState<GrainWeighing[]>([]);
  const [selected, setSelected] = useState<number>();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const source = rows.find((row) => row.id === selected);

  async function show() {
    setOpen(true);
    setError("");
    setRows([]);
    setSelected(undefined);
    setReason("");
    onBusyChange?.(true);
    if (!item && wagon) {
      setBusy(true);
      try {
        const { data } = await api.get<GrainUnassignedWeighing[]>(
          `/grain/unassigned-weighings/?status=assigned&wagon=${wagon.id}`,
        );
        const entries = data.filter((row) => row.action === "entry");
        if (entries.length !== 1) throw new Error("Не найдена однозначная исходная запись");
        setResolvedItem(entries[0]);
      } catch {
        setError("Не удалось найти исходное взвешивание. Обновите рейс и попробуйте ещё раз.");
      } finally {
        setBusy(false);
      }
    }
  }
  async function search() {
    if (!resolvedItem || busy) return;
    setBusy(true);
    setError("");
    setSelected(undefined);
    setRows([]);
    try {
      const { data } = await api.get<GrainWeighing[]>(
        `/grain/unassigned-weighings/${resolvedItem.id}/tare-candidates/`,
        { params: { number } },
      );
      setRows(data);
      if (!data.length)
        setError(
          "До этого выезда не найдено подходящей подтверждённой тары этого номера. Более поздние заезды здесь не показываются.",
        );
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!resolvedItem || !source || reason.trim().length < 5 || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/unassigned-weighings/${resolvedItem.id}/historical-exit/`, {
        number,
        reference_record: source.id,
        reason: reason.trim(),
      });
      setOpen(false);
      onBusyChange?.(false);
      onChanged();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button disabled={disabled} variant="outline" size="sm" onClick={() => void show()}>
        {wagon ? "Исправить как выезд" : "Выезд с сохранённой тарой"}
      </Button>
      <ConfirmDialog
        open={open}
        title="Выезд с сохранённой тарой"
        description="Сверьте номер и исходную запись тары. Это прежний вес: сегодняшнее взвешивание пустой машины не подтверждено. Источник и автор исходной записи сохранятся."
        confirmLabel="Сохранить и завершить вывоз"
        confirmVariant="default"
        busy={busy}
        error={error}
        confirmDisabled={!source || reason.trim().length < 5 || !resolvedItem}
        onConfirm={() => void save()}
        onClose={() => {
          if (!busy) {
            setOpen(false);
            onBusyChange?.(false);
          }
        }}
      >
        <div className="space-y-3">
          <div className="flex gap-2">
            <Input
              aria-label="Номер машины для поиска тары"
              value={number}
              disabled={busy}
              onChange={(e) => {
                setNumber(e.target.value);
                setRows([]);
                setSelected(undefined);
              }}
            />
            <Button disabled={busy || !resolvedItem || !number.trim()} onClick={() => void search()}>
              Найти тару
            </Button>
          </div>
          {resolvedItem && (
            <p>
              Выезд: {formatKg(resolvedItem.weight_kg)} · {formatDateTime(resolvedItem.stable_weight_at)}
            </p>
          )}
          <div className="max-h-64 space-y-2 overflow-auto">
            {rows.map((row) => (
              <label key={row.id} className="flex cursor-pointer items-center gap-3 rounded-lg border p-2">
                <input
                  type="radio"
                  name="historical-tare"
                  disabled={busy}
                  checked={selected === row.id}
                  onChange={() => setSelected(row.id)}
                />
                {row.photo_url && (
                  // eslint-disable-next-line @next/next/no-img-element -- signed evidence URL
                  <img
                    src={apiFileUrl(row.photo_url) ?? ""}
                    alt={`Тара от ${formatDateTime(row.created_at)}`}
                    className="h-20 w-28 rounded object-cover"
                  />
                )}
                <span>
                  {formatKg(row.weight_kg)}
                  <span className="block text-xs">{formatDateTime(row.created_at)}</span>
                  <span className="block text-xs text-[var(--muted-foreground)]">
                    {row.source === "manual" ? "Тара введена вручную" : "Вес получен с весов"}
                    {row.operator_name ? ` · ${row.operator_name}` : ""}
                    {!row.photo_url ? " · без фото" : ""}
                  </span>
                  {row.manual_reason && <span className="block text-xs">{row.manual_reason}</span>}
                </span>
              </label>
            ))}
          </div>
          {source && resolvedItem && (
            <p className="font-semibold">Нетто: {formatKg(resolvedItem.weight_kg - source.weight_kg)}</p>
          )}
          <Input
            aria-label="Причина использования сохранённой тары"
            placeholder="Причина исправления или использования прежней тары"
            value={reason}
            maxLength={300}
            disabled={busy}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      </ConfirmDialog>
    </>
  );
}
