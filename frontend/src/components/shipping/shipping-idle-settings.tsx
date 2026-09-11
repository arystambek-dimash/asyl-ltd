"use client";

import { useId, useState, type FormEvent } from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { ShippingSessionSettings } from "@/lib/shipping-sessions";
import { useApi } from "@/lib/use-api";

const INPUT_CLASS = "h-10 w-full rounded-md border bg-[var(--background)] px-3 text-sm";

/** Простой один на все конвейеры: без мешков дольше него отрезок погрузки закрывается. */
export function ShippingIdleSettings() {
  const settings = useApi<ShippingSessionSettings>("/cameras/shipping-session-settings/");
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inputId = useId();
  const data = settings.data;
  if (!data) return null;
  if (!data.can_manage) {
    return (
      <p className="text-sm text-[var(--muted-foreground)]">
        Закрытие по простою: {data.idle_timeout_seconds / 60} мин.
      </p>
    );
  }
  const seconds = Number(minutes) * 60;
  const valid = minutes.trim() !== "" && Number.isInteger(seconds) && seconds >= 30 && seconds <= 86_400;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setError("");
    try {
      const response = await api.patch<ShippingSessionSettings>("/cameras/shipping-session-settings/", {
        idle_timeout_seconds: seconds,
      });
      settings.setData(response.data);
      setOpen(false);
    } catch (failure) {
      setError(apiError(failure) || "Не удалось изменить настройку. Проверьте права и обновите данные.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setMinutes(String(data.idle_timeout_seconds / 60));
          setError("");
          setOpen(true);
        }}
      >
        <Settings2 className="size-4" />
        Простой: {data.idle_timeout_seconds / 60} мин.
      </Button>
      <Modal
        open={open}
        onClose={() => {
          if (!saving) setOpen(false);
        }}
        title="Закрытие отрезка по простою"
      >
        <form onSubmit={(event) => void submit(event)} className="space-y-4">
          <p className="text-sm">
            Если мешки не поступают указанное время, отрезок закрывается автоматически. Следующий мешок начинает новый
            отрезок.
          </p>
          <label htmlFor={inputId} className="block text-sm font-medium">
            Простой, минут
          </label>
          <input
            id={inputId}
            type="number"
            min="0.5"
            max="1440"
            step="any"
            className={INPUT_CLASS}
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            disabled={saving}
            required
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            От 0,5 до 1440 минут. По умолчанию — 5 минут. Новое значение применяется к новым отрезкам; открытые
            сохраняют прежнюю настройку.
          </p>
          {error && <ErrorAlert message={error} />}
          <Button type="submit" disabled={saving || !valid}>
            {saving ? "Сохраняем…" : "Сохранить настройку"}
          </Button>
        </form>
      </Modal>
    </>
  );
}
