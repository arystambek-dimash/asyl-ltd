"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import type { GrainSupply, GrainWagon } from "@/lib/types";
import { Camera } from "lucide-react";
import { useState } from "react";

export function ArrivalForm({
  supplies,
  initialSupply,
  onDone,
  onCancel,
}: {
  supplies: GrainSupply[];
  initialSupply?: number | null;
  onDone: (wagon: GrainWagon) => void;
  onCancel: () => void;
}) {
  const [number, setNumber] = useState("");
  const [supply, setSupply] = useState(initialSupply ? String(initialSupply) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainWagon>("/grain/wagons/arrive/", {
        number,
        supply: Number(supply),
      });
      onDone(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-sky-200 bg-[#10222a] p-4 text-white">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-sky-400/10 text-sky-300">
            <Camera className="size-5" />
          </span>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-sky-300">Основной источник</p>
            <p className="mt-1 text-sm font-bold">Номер приходит от камеры проходной</p>
          </div>
        </div>
      </div>
      <div>
        <Label htmlFor="arrival-supply">Ожидаемый приход *</Label>
        <Select id="arrival-supply" value={supply} onChange={(event) => setSupply(event.target.value)}>
          <option value="">Выберите приход</option>
          {supplies.map((item) => (
            <option key={item.id} value={item.id}>
              #{item.id} · {item.supplier} · {item.grain_type_name} → {item.assigned_silo_name}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="arrival-number">Номер поезда / вагона *</Label>
        <Input
          id="arrival-number"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          placeholder="Распознанный номер"
          autoFocus
        />
        <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">
          Ручной ввод — резервный вариант, пока OCR-сервис не передал номер автоматически.
        </p>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !number.trim() || !supply} onClick={() => void submit()}>
          {busy ? "Регистрация…" : "Зарегистрировать приход"}
        </Button>
      </div>
    </div>
  );
}
