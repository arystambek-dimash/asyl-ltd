"use client";

import { useState } from "react";
import { Check, LoaderCircle, Pencil, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, apiError } from "@/lib/api";
import { isPassagePlateMissing } from "@/lib/grain";
import type { GrainWagon } from "@/lib/types";

/**
 * Номер вывоза, который камера не прочла (или прочла неверно), оператор
 * дописывает прямо в карточке. Ошибка показывается рядом с полем: сама
 * форма маленькая, и уводить сообщение на страницу нельзя.
 */
export function PassageNumberEditor({
  wagon,
  canEdit,
  onChanged,
}: {
  wagon: GrainWagon;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const missing = isPassagePlateMissing(wagon);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(wagon.number);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (wagon.direction !== "passage") return null;
  const finished = ["completed", "cancelled", "return_to_supplier"].includes(wagon.status);

  async function save() {
    setBusy(true);
    setError("");
    try {
      await api.patch(`/grain/wagons/${wagon.id}/number/`, { number: value });
      setOpen(false);
      onChanged();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        {missing && <Badge tone="warning">номер не распознан</Badge>}
        {canEdit && !finished && (
          <Button
            size="sm"
            variant={missing ? "default" : "outline"}
            onClick={() => {
              setValue(wagon.number);
              setError("");
              setOpen(true);
            }}
          >
            <Pencil /> {missing ? "Указать номер" : "Изменить номер"}
          </Button>
        )}
      </span>
    );
  }

  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <Input
        aria-label="Номер машины"
        value={value}
        onChange={(event) => setValue(event.target.value.toUpperCase())}
        placeholder="465BDS13"
        className="h-9 w-40 font-mono uppercase"
        autoFocus
        maxLength={30}
      />
      <Button size="sm" type="submit" disabled={busy || !value.trim()}>
        {busy ? <LoaderCircle className="animate-spin" /> : <Check />} Сохранить
      </Button>
      <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
        <X /> Отмена
      </Button>
      {error && (
        <span role="alert" className="basis-full text-xs text-[var(--destructive)]">
          {error}
        </span>
      )}
    </form>
  );
}
