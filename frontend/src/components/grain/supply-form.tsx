"use client";

import { Button } from "@/components/ui/button";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { formatKg } from "@/lib/grain";
import type { GrainSilo, GrainSupply, GrainType } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { ArrowRight, Check, Plus } from "lucide-react";
import { useMemo, useState } from "react";

function GrainTypeCreator({ onCreated, onCancel }: { onCreated: (type: GrainType) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("#B78132");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainType>("/grain/types/", { name, description, color });
      onCreated(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-amber-700">Новый справочник</p>
          <p className="mt-1 text-sm font-bold">Создать тип зерна</p>
        </div>
        <button type="button" onClick={onCancel} className="text-xs text-slate-500 hover:text-slate-900">
          Закрыть
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <div>
          <Label>Название *</Label>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Пшеница продовольственная"
          />
        </div>
        <div>
          <Label>Описание</Label>
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Краткое назначение"
          />
        </div>
        <div>
          <Label>Цвет</Label>
          <input
            type="color"
            value={color}
            onChange={(event) => setColor(event.target.value.toUpperCase())}
            className="h-10 w-14 cursor-pointer rounded-md border border-amber-200 bg-white p-1"
          />
        </div>
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      <div className="mt-3 flex justify-end">
        <Button size="sm" disabled={busy || !name.trim()} onClick={() => void submit()}>
          <Plus className="size-4" /> {busy ? "Создание…" : "Создать тип"}
        </Button>
      </div>
    </div>
  );
}

export function SupplyForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const {
    data: types,
    loading: typesLoading,
    error: typesError,
    reload: reloadTypes,
  } = useApi<GrainType[]>("/grain/types/");
  const {
    data: silos,
    loading: silosLoading,
    error: silosError,
    reload: reloadSilos,
  } = useApi<GrainSilo[]>("/grain/silos/");
  const [supplier, setSupplier] = useState("");
  const [grainType, setGrainType] = useState("");
  const [expectedTons, setExpectedTons] = useState("");
  const [siloId, setSiloId] = useState("");
  const [note, setNote] = useState("");
  const [creatingType, setCreatingType] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const suitableSilos = useMemo(
    () =>
      (silos ?? []).filter(
        (silo) =>
          silo.status === "active" && (!grainType || silo.silo_type == null || silo.silo_type === Number(grainType)),
      ),
    [grainType, silos],
  );

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post<GrainSupply>("/grain/supplies/", {
        supplier: supplier.trim(),
        grain_type: Number(grainType),
        assigned_silo: Number(siloId),
        expected_total_kg: Math.round(Number(expectedTons) * 1000),
        simple_flow: true,
        note: note.trim(),
      });
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!types || !silos) {
    return (
      <DataGate
        loading={typesLoading || silosLoading}
        error={typesError || silosError}
        onRetry={() => void Promise.all([reloadTypes(), reloadSilos()])}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2">
        {[
          ["1", "Поставщик"],
          ["2", "Тип зерна"],
          ["3", "Вес"],
          ["4", "Силос"],
        ].map(([number, label]) => (
          <div key={number} className="rounded-xl border border-slate-200 bg-slate-50 px-2 py-2 text-center">
            <span className="mx-auto flex size-5 items-center justify-center rounded-full bg-slate-900 text-[10px] font-bold text-white">
              {number}
            </span>
            <p className="mt-1 text-[10px] font-semibold text-slate-600">{label}</p>
          </div>
        ))}
      </div>

      <div>
        <Label htmlFor="grain-supplier">Название поставщика *</Label>
        <Input
          id="grain-supplier"
          value={supplier}
          onChange={(event) => setSupplier(event.target.value)}
          placeholder="ТОО Колос"
          autoFocus
        />
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between gap-3">
          <Label htmlFor="grain-type" className="mb-0">
            Тип зерна *
          </Label>
          <button
            type="button"
            className="text-xs font-semibold text-amber-700 hover:text-amber-900"
            onClick={() => setCreatingType((current) => !current)}
          >
            + Создать новый тип
          </button>
        </div>
        <Select
          id="grain-type"
          value={grainType}
          onChange={(event) => {
            setGrainType(event.target.value);
            setSiloId("");
          }}
        >
          <option value="">Выберите тип зерна</option>
          {types.map((type) => (
            <option key={type.id} value={type.id}>
              {type.name}
            </option>
          ))}
        </Select>
      </div>

      {creatingType && (
        <GrainTypeCreator
          onCancel={() => setCreatingType(false)}
          onCreated={(type) => {
            reloadTypes().then(() => setGrainType(String(type.id)));
            setCreatingType(false);
          }}
        />
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="grain-expected-weight">Ожидаемый вес, тонн *</Label>
          <Input
            id="grain-expected-weight"
            type="number"
            min="0.001"
            step="0.1"
            value={expectedTons}
            onChange={(event) => setExpectedTons(event.target.value)}
            placeholder="68.3"
          />
        </div>
        <div>
          <Label htmlFor="grain-silo">Силос назначения *</Label>
          <Select
            id="grain-silo"
            value={siloId}
            onChange={(event) => setSiloId(event.target.value)}
            disabled={!grainType}
          >
            <option value="">{grainType ? "Выберите силос" : "Сначала выберите тип зерна"}</option>
            {suitableSilos.map((silo) => (
              <option key={silo.id} value={silo.id}>
                {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {grainType && suitableSilos.length === 0 && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Для этого типа зерна нет доступного силоса. Назначьте тип силосу или проверьте свободное место.
        </p>
      )}

      <div>
        <Label htmlFor="grain-note">Комментарий</Label>
        <Input
          id="grain-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Необязательно"
        />
      </div>

      <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-3 text-sm text-emerald-950">
        <div className="flex items-start gap-2">
          <Check className="mt-0.5 size-4 shrink-0 text-emerald-700" />
          После создания приход сразу появится в «Ожидаются». Номер поезда заполнит камера на проходной.
        </div>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button
          disabled={busy || !supplier.trim() || !grainType || !expectedTons || !siloId}
          onClick={() => void submit()}
        >
          {busy ? "Создание…" : "Создать приход"} <ArrowRight className="size-4" />
        </Button>
      </div>
    </div>
  );
}

/**
 * Регистрация вывоза. Ожидаемый вес не спрашиваем: сколько заберут — решают
 * на погрузке, факт станет известен только на выездных весах.
 */
