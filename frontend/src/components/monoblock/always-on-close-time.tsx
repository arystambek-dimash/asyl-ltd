"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Panel, StatusChip } from "@/components/monoblock/ui";
import type { AlwaysOnProductionPayload } from "@/lib/types";
import { apiError } from "@/lib/api";
import { formatIsoDate } from "@/lib/utils";

/**
 * «Автоприход»: час закрытия смены, следующий запуск и готовность. Час меняет
 * суперпользователь: с идущей смены, если до её закрытия больше 10 минут,
 * иначе — со следующей (решает бэкенд, production_runs.set_close_time).
 */
export function AlwaysOnCloseTimeCard({
  closeTime,
  planned,
  nextRun,
  timezone,
  needsSetup,
  onSave,
}: {
  closeTime: string;
  planned: AlwaysOnProductionPayload["planned_close_time"];
  nextRun: string;
  timezone: string;
  needsSetup: boolean;
  /** Только у суперпользователя. Бросает ошибку запроса — карточка показывает её у себя. */
  onSave?: (closeTime: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function cancel() {
    setDraft(null);
    setError("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!onSave || !draft) return;
    setSaving(true);
    setError("");
    try {
      await onSave(draft);
      setDraft(null);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel className="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <div className="flex items-center gap-2">
            <Eyebrow>Автоприход</Eyebrow>
            {onSave && draft === null && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                onClick={() => setDraft(planned?.close_time ?? closeTime)}
              >
                <Pencil className="size-3.5" /> Изменить
              </Button>
            )}
          </div>
          {draft === null ? (
            <>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-5xl font-black tabular-nums tracking-tight text-slate-900">{closeTime}</span>
                <span className="text-sm font-medium text-slate-400">каждый день</span>
              </div>
              {planned && (
                <p className="mt-1.5 text-sm font-medium text-slate-600">
                  Со смены {formatIsoDate(planned.from_day)} — в {planned.close_time}
                </p>
              )}
            </>
          ) : (
            <form onSubmit={submit} className="mt-2 grid max-w-sm gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="time"
                  step={60}
                  required
                  aria-label="Время автоприхода"
                  value={draft}
                  disabled={saving}
                  onChange={(event) => setDraft(event.target.value)}
                  className="h-10 w-32 text-lg tabular-nums"
                />
                <Button type="submit" size="sm" disabled={saving || !draft}>
                  {saving ? "Сохраняем…" : "Сохранить"}
                </Button>
                <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={cancel}>
                  Отмена
                </Button>
              </div>
              <p className="text-xs text-slate-500">
                Действует с текущей смены, если до её закрытия больше 10 минут, иначе — со следующей. Прошлые смены не
                меняются.
              </p>
              {error && (
                <p role="alert" className="text-sm text-[var(--destructive)]">
                  {error}
                </p>
              )}
            </form>
          )}
        </div>
        <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
          <div>
            <Eyebrow>Следующий запуск</Eyebrow>
            <div className="mt-1.5 font-semibold tabular-nums text-slate-800">{nextRun}</div>
            <div className="mt-0.5 text-[11px] text-slate-400">{timezone}</div>
          </div>
          <div>
            <Eyebrow>Готовность</Eyebrow>
            <div className="mt-2">
              {needsSetup ? (
                <StatusChip tone="error">нужна настройка</StatusChip>
              ) : (
                <StatusChip tone="ok">готово</StatusChip>
              )}
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
