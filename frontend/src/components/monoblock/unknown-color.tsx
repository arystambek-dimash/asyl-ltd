"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { LoaderCircle, Palette } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { apiError } from "@/lib/api";
import { colorMeta, isUndeterminedColor, normalizedColor } from "@/lib/monoblock-colors";
import type {
  AlwaysOnInferred,
  AlwaysOnInferredMethod,
  AlwaysOnProductMapping,
  AlwaysOnUnknownColorInput,
} from "@/lib/types";
import { bagsLabel, cn, formatIsoDate } from "@/lib/utils";

const METHOD_ORDER: AlwaysOnInferredMethod[] = ["neighbors", "votes", "manual"];
const METHOD_LABELS: Record<AlwaysOnInferredMethod, string> = {
  neighbors: "по соседям",
  votes: "по голосам",
  manual: "вручную",
};
const METHOD_HINTS: Record<AlwaysOnInferredMethod, string> = {
  neighbors: "цвет взят у соседних мешков той же партии",
  votes: "цвет выбран по отдельным кадрам линий проверки",
  manual: "цвет указал оператор",
};
function inferredEntries(inferred: AlwaysOnInferred | undefined) {
  return METHOD_ORDER.map((method) => [method, inferred?.[method] ?? 0] as const).filter(([, count]) => count > 0);
}

/** «по соседям · 2» или «по соседям 3 · по голосам 1»; пусто, если всё распознала камера. */
function inferredLabel(inferred: AlwaysOnInferred | undefined): string {
  const entries = inferredEntries(inferred);
  if (entries.length === 1) {
    const [method, count] = entries[0];
    return `${METHOD_LABELS[method]} · ${count}`;
  }
  return entries.map(([method, count]) => `${METHOD_LABELS[method]} ${count}`).join(" · ");
}

export function unresolvedBagsLabel(bags: number): string {
  return `Цвет не определён: ${bagsLabel(bags)}`;
}

/** Ненавязчивая пометка: часть мешков этого цвета определила CRM, а не камера. */
export function InferredBadge({ inferred, className }: { inferred?: AlwaysOnInferred; className?: string }) {
  const entries = inferredEntries(inferred);
  if (!entries.length) return null;
  const title =
    "Камера не определила цвет этих мешков: " +
    entries.map(([method, count]) => `${bagsLabel(count)} — ${METHOD_HINTS[method]}`).join("; ");
  return (
    <span
      data-inferred-badge
      title={title}
      className={cn(
        "inline-flex w-fit shrink-0 items-center rounded border border-dashed border-[var(--border)] px-1.5 py-px text-[10px] font-medium leading-4 text-[var(--muted-foreground)]",
        className,
      )}
    >
      {inferredLabel(inferred)}
    </span>
  );
}

interface UnknownColorDialogProps {
  open: boolean;
  businessDay: string;
  pendingBags: number;
  /** Смена уже оприходована: мешки уйдут отдельным приходом. */
  posted: boolean;
  mappings: AlwaysOnProductMapping[];
  onClose: () => void;
  /** Бросает ошибку запроса — окно покажет её у себя и останется открытым. */
  onSubmit: (input: AlwaysOnUnknownColorInput) => Promise<void>;
}

/** «Указать цвет» для мешков смены, которые не определили ни камера, ни соседи. */
export function UnknownColorDialog({
  open,
  businessDay,
  pendingBags,
  posted,
  mappings,
  onClose,
  onSubmit,
}: UnknownColorDialogProps) {
  const choices = useMemo(
    () => mappings.filter((row) => row.product !== null && row.product_label && !isUndeterminedColor(row.color)),
    [mappings],
  );
  const [color, setColor] = useState("");
  const [bags, setBags] = useState(String(pendingBags));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fieldId = useId();

  useEffect(() => {
    if (!open) return;
    setColor(choices[0] ? normalizedColor(choices[0].color) : "");
    setBags(String(pendingBags));
    setReason("");
    setError("");
    // Reset only when the dialog opens for a shift, not on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, businessDay]);

  const count = Number(bags);
  const bagsValid = Number.isInteger(count) && count >= 1 && count <= pendingBags;
  const reasonValid = reason.trim().length >= 5;
  const canSubmit = Boolean(color) && bagsValid && reasonValid && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit({ business_day: businessDay, color, bags: count, reason: reason.trim() });
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      eyebrow={`Смена ${formatIsoDate(businessDay)}`}
      title="Указать цвет"
      description={`${unresolvedBagsLabel(pendingBags)}. Камера не смогла определить цвет, а соседние мешки не дают однозначного ответа.`}
      className="max-w-lg"
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={!canSubmit} onClick={() => void submit()}>
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Palette className="size-4" />}
            Указать цвет
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {choices.length ? (
          <div className="grid gap-1.5">
            <Label htmlFor={`${fieldId}-color`}>Цвет мешков</Label>
            <Select
              id={`${fieldId}-color`}
              value={color}
              disabled={busy}
              onChange={(event) => setColor(event.target.value)}
            >
              {choices.map((row) => (
                <option key={row.color} value={normalizedColor(row.color)}>
                  {colorMeta(row.color).label} → {row.product_label}
                </option>
              ))}
            </Select>
          </div>
        ) : (
          <p className="rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
            Сначала привяжите товар к цвету в разделе «Куда приходовать».
          </p>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor={`${fieldId}-bags`}>Мешков</Label>
          <Input
            id={`${fieldId}-bags`}
            type="number"
            inputMode="numeric"
            min={1}
            max={pendingBags}
            step={1}
            value={bags}
            disabled={busy}
            aria-invalid={bags !== "" && !bagsValid}
            onChange={(event) => setBags(event.target.value)}
            className="w-32 tabular-nums"
          />
          <span
            className={cn(
              "text-xs",
              bags !== "" && !bagsValid ? "text-[var(--destructive)]" : "text-[var(--muted-foreground)]",
            )}
          >
            Не больше {pendingBags}
          </span>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${fieldId}-reason`}>Причина</Label>
          <textarea
            id={`${fieldId}-reason`}
            maxLength={500}
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: проверено по записи камеры"
            className="min-h-20 w-full resize-y rounded-md border border-[var(--input)] bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus-visible:border-[var(--ring)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20"
          />
          <span className="text-xs text-[var(--muted-foreground)]">
            Минимум 5 символов. Автор и причина сохранятся в журнале.
          </span>
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          {posted
            ? "Смена уже оприходована: мешки поступят на склад отдельным приходом."
            : "Мешки войдут в приход смены вместе с остальной продукцией."}
        </p>
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
