"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Check, LoaderCircle, RotateCcw } from "lucide-react";

import { ColorDot } from "@/components/monoblock/ui";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { apiError } from "@/lib/api";
import { restoreFocus } from "@/lib/focus";
import { colorMeta, isUndeterminedColor, PALETTE_COLORS } from "@/lib/monoblock-colors";
import type { AlwaysOnColorAnalytics } from "@/lib/types";
import { cn, formatCount, PHONE_INPUT_TEXT } from "@/lib/utils";

const MAX_BAGS = 1_000_000;

/** Введённое число и показанное на экране в момент правки этого цвета. */
type Draft = { value: string; seen: number };

function parsedBags(value: string): number | null {
  if (!/^\d{1,7}$/.test(value)) return null;
  const bags = Number(value);
  return bags <= MAX_BAGS ? bags : null;
}

/**
 * Кнопка «Изменить» скрыта, пока открыт редактор, а сам редактор исчезает
 * после «Сохранить»/«Отмена» — фокус падал на <body>, и Tab уводил из окна
 * камеры. Возвращает ref для кнопки: если при закрытии фокус потерялся, он
 * возвращается на неё (фокус, который пользователь сам перевёл, не трогаем).
 */
export function useEditorFocusReturn(editing: boolean) {
  const trigger = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(editing);
  useEffect(() => {
    const focusLost = !document.activeElement || document.activeElement === document.body;
    if (wasEditing.current && !editing && focusLost) restoreFocus(trigger.current);
    wasEditing.current = editing;
  }, [editing]);
  return trigger;
}

/**
 * Ручная правка количества мешков по цветам (только суперпользователь):
 * за день аналитики камеры или для одной сессии отгрузки. Камера продолжает
 * считать сверху. В поле — показанное сейчас число. Цвет отправляется, только
 * если введённое число отличается от показанного в момент правки: мешки,
 * досчитанные камерой, пока редактор открыт, не перетираются — ни у нетронутых
 * цветов, ни у набранных заново прежним числом, ни после «Как у камеры».
 */
export function ColorCountsEditor({
  items,
  cameraCounts,
  total,
  onSave,
  onCancel,
  hint,
}: {
  /** Цвета, показанные сейчас (с прежними правками). */
  items: AlwaysOnColorAnalytics[];
  /** Сколько насчитала сама камера. */
  cameraCounts: Record<string, number>;
  /** Показанный сейчас итог. */
  total: number;
  /** Бросает ошибку запроса — она остаётся в редакторе. */
  onSave: (colors: Record<string, number>) => Promise<void>;
  onCancel: () => void;
  hint?: string;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const invalidHintId = useId();

  const shown = Object.fromEntries(items.map((item) => [item.color, item.total]));
  const shownOf = (color: string) => shown[color] ?? 0;
  const cameraOf = (color: string) => cameraCounts[color] ?? 0;
  const byCount = (counts: Record<string, number>) => Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  // Показанные цвета по убыванию, затем цвета камеры и палитры; «Не определён» — в конце.
  const colors = [
    ...new Set([...byCount(shown), ...byCount(cameraCounts), ...PALETTE_COLORS, ...Object.keys(drafts)]),
  ].sort((a, b) => Number(isUndeterminedColor(a)) - Number(isUndeterminedColor(b)));
  const valueOf = (color: string) => drafts[color]?.value ?? String(shownOf(color));

  const invalid = colors.some((color) => parsedBags(valueOf(color)) === null);
  const changes: Record<string, number> = {};
  for (const [color, { value, seen }] of Object.entries(drafts)) {
    const bags = parsedBags(value);
    if (bags !== null && bags !== seen) changes[color] = bags;
  }
  const changed = Object.keys(changes).length > 0;
  // Итог меняется ровно на видимую сейчас разницу; ниже нуля сервер его не опускает.
  const nextTotal = Math.max(
    0,
    Object.entries(changes).reduce((sum, [color, bags]) => sum + bags - shownOf(color), total),
  );
  const differsFromCamera = colors.some((color) => valueOf(color) !== String(cameraOf(color)));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || invalid || !changed) return;
    setSaving(true);
    setError("");
    try {
      await onSave(changes);
    } catch (failure) {
      setError(apiError(failure) || "Не удалось сохранить. Проверьте права и обновите данные.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label="Исправление цветов мешков" onSubmit={(event) => void submit(event)} className="space-y-3">
      {hint && <p className="text-xs text-[var(--muted-foreground)]">{hint}</p>}
      <div className="divide-y divide-[var(--border)]">
        {colors.map((color, index) => {
          const { label, dot } = colorMeta(color);
          const value = valueOf(color);
          const valid = parsedBags(value) !== null;
          return (
            <label key={color} className="flex items-center gap-3 py-2 first:pt-0">
              <ColorDot className={dot} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{label}</span>
                {value !== String(cameraOf(color)) && (
                  <span className="block text-xs tabular-nums text-[var(--muted-foreground)]">
                    камера: {formatCount(cameraOf(color))}
                  </span>
                )}
              </span>
              <Input
                aria-label={`Мешков: ${label}`}
                inputMode="numeric"
                autoComplete="off"
                autoFocus={index === 0}
                value={value}
                disabled={saving}
                aria-invalid={!valid}
                aria-describedby={valid ? undefined : invalidHintId}
                onChange={(event) => {
                  const typed = event.target.value.trim();
                  setDrafts((current) => ({
                    ...current,
                    [color]: { value: typed, seen: current[color]?.seen ?? shownOf(color) },
                  }));
                }}
                className={cn("h-9 w-24 text-right tabular-nums", PHONE_INPUT_TEXT)}
              />
            </label>
          );
        })}
      </div>
      {invalid && (
        <p id={invalidHintId} className="text-xs text-[var(--destructive)]">
          Укажите целое число мешков от 0 до {formatCount(MAX_BAGS)}.
        </p>
      )}
      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
        <p className="mr-auto text-sm text-[var(--muted-foreground)]">
          Итого: <span className="font-semibold tabular-nums text-[var(--foreground)]">{formatCount(nextTotal)}</span>{" "}
          меш.
        </p>
        {differsFromCamera && (
          <Button
            variant="ghost"
            size="sm"
            disabled={saving}
            onClick={() =>
              setDrafts(
                Object.fromEntries(
                  colors
                    .filter((color) => cameraOf(color) !== shownOf(color))
                    .map((color) => [color, { value: String(cameraOf(color)), seen: shownOf(color) }]),
                ),
              )
            }
          >
            <RotateCcw /> Как у камеры
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={saving} onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" size="sm" disabled={saving || invalid || !changed}>
          {saving ? <LoaderCircle className="animate-spin" /> : <Check />} Сохранить
        </Button>
      </div>
    </form>
  );
}
