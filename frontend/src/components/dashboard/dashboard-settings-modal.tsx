"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import type { DashboardSettings, Department, Warehouse } from "@/lib/types";

type Option = { id: number; name: string };

function CheckList({
  title,
  hint,
  options,
  chosen,
  onToggle,
}: {
  title: string;
  hint: string;
  options: Option[];
  chosen: Set<number>;
  onToggle: (id: number) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-sm font-semibold">{title}</legend>
      <p className="text-[12px] text-[var(--muted-foreground)]">{hint}</p>
      {options.map((option) => (
        <label key={option.id} className="flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm">
          <input type="checkbox" checked={chosen.has(option.id)} onChange={() => onToggle(option.id)} />
          {option.name}
        </label>
      ))}
    </fieldset>
  );
}

function toggled(set: Set<number>, id: number): Set<number> {
  const next = new Set(set);
  if (!next.delete(id)) next.add(id);
  return next;
}

/**
 * Что показывает «Главная» (суперпользователь): отделы — для отгрузок, погрузки
 * и денег, склады — для остатка. Ничего не отмечено — видно всё. Ответ PUT
 * экран применяет сразу, а не перечитывает опрашиваемую сводку.
 */
export function DashboardSettingsModal({
  settings,
  onClose,
  onSaved,
}: {
  settings: DashboardSettings;
  onClose: () => void;
  onSaved: (settings: DashboardSettings) => void;
}) {
  const { data: departments } = useApi<Department[]>("/departments/");
  const { data: warehouses } = useApi<Warehouse[]>("/warehouses/");
  const [departmentIds, setDepartmentIds] = useState(() => new Set(settings.departments.map((item) => item.id)));
  const [warehouseIds, setWarehouseIds] = useState(() => new Set(settings.warehouses.map((item) => item.id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.put<DashboardSettings>("/dashboard/settings/", {
        departments: [...departmentIds],
        warehouses: [...warehouseIds],
      });
      onSaved(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      variant="sheet"
      className="max-w-lg"
      eyebrow="Главная и ТВ"
      title="Что показывать"
      description="Выбор общий для всех, у кого открыта Главная, и для всех телевизоров."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy || !departments || !warehouses} onClick={() => void save()}>
            {busy ? "Сохраняем…" : "Сохранить"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {error && <ErrorAlert message={error} />}
        <CheckList
          title="Отделы"
          hint="Отгрузки, погрузка, поступления и долг считаются только по заказам этих отделов. Если ничего не отмечено, видны все отделы."
          options={(departments ?? []).filter((item) => item.is_active)}
          chosen={departmentIds}
          onToggle={(id) => setDepartmentIds((current) => toggled(current, id))}
        />
        <CheckList
          title="Склады"
          hint="Остаток показывается по одному складу. Если отмечено несколько, на Главной появится выбор склада. Если ничего не отмечено, видны все склады."
          options={(warehouses ?? []).filter((item) => item.is_active)}
          chosen={warehouseIds}
          onToggle={(id) => setWarehouseIds((current) => toggled(current, id))}
        />
      </div>
    </Modal>
  );
}
