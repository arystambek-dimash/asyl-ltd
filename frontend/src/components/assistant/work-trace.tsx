"use client";
import { useState } from "react";
import { Brain, Check, Code2, Database, Loader2, Search, Table2, X, type LucideIcon } from "lucide-react";
import { formatAssistantCell, type TraceStep } from "@/lib/assistant";
import { cn, formatCount, pluralRu } from "@/lib/utils";

const KIND_ICON: Record<TraceStep["kind"], LucideIcon> = {
  think: Brain,
  search: Search,
  schema: Table2,
  sql: Database,
};

const SECONDS = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Длительность: «84 мс» до секунды, дальше «5,8 с». */
export function formatSeconds(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} мс` : `${SECONDS.format(ms / 1000)} с`;
}

function stepMeta(step: TraceStep): string[] {
  const meta = [];
  if (step.ms !== undefined) meta.push(formatSeconds(step.ms));
  if (step.row_count !== undefined) {
    meta.push(`${formatCount(step.row_count)} ${pluralRu(step.row_count, ["строка", "строки", "строк"])}`);
  }
  if (step.found !== undefined) meta.push(`найдено ${step.found}`);
  return meta;
}

function StepIcon({ step }: { step: TraceStep }) {
  const Icon = KIND_ICON[step.kind];
  return (
    <span
      className={cn(
        "relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-[var(--card)] transition-colors",
        step.status === "running" && "border-[var(--ring)] text-[var(--ring)]",
        step.status === "done" && "border-[var(--border)] text-[var(--muted-foreground)]",
        step.status === "error" && "border-[var(--destructive)]/40 text-[var(--destructive)]",
      )}
    >
      {step.status === "running" ? (
        <Loader2 className="size-4 animate-spin" />
      ) : step.status === "error" ? (
        <X className="size-4" />
      ) : (
        <Icon className="size-4" />
      )}
      {step.status === "done" && (
        <Check className="absolute -bottom-0.5 -right-0.5 size-3.5 rounded-full bg-[var(--success)] p-0.5 text-white" />
      )}
    </span>
  );
}

function Preview({ preview }: { preview: NonNullable<TraceStep["preview"]> }) {
  if (preview.rows.length === 0) return null;
  return (
    <div className="mt-2 overflow-x-auto rounded-md border border-[var(--border)] animate-fade-up">
      <table className="w-full text-[11px]">
        <thead className="text-[var(--muted-foreground)]">
          <tr>
            {preview.columns.map((column, index) => (
              <th key={index} className="whitespace-nowrap px-2 py-1 text-left font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-t border-[var(--border)]">
              {row.map((cell, index) => (
                <td key={index} className="whitespace-nowrap px-2 py-1 tabular-nums">
                  {formatAssistantCell(cell, preview.columns[index] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TraceItem({ step, last, live }: { step: TraceStep; last: boolean; live: boolean }) {
  const [showSql, setShowSql] = useState(false);
  const sqlVisible = !!step.sql && (live || showSql);
  const meta = stepMeta(step);
  return (
    <li className="relative flex gap-3 animate-fade-up">
      {!last && <span aria-hidden className="absolute bottom-[-12px] left-[15px] top-8 w-px bg-[var(--border)]" />}
      <StepIcon step={step} />
      <div className="min-w-0 flex-1 pt-1">
        <p className="text-sm">
          {step.text}
          {meta.length > 0 && (
            <span className="ml-2 text-xs tabular-nums text-[var(--muted-foreground)]">{meta.join(" · ")}</span>
          )}
        </p>
        {step.tables && step.tables.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {step.tables.map((table) => (
              <span
                key={table}
                className="rounded-md bg-[var(--muted)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--muted-foreground)]"
              >
                {table}
              </span>
            ))}
          </div>
        )}
        {step.sql && !live && (
          <button
            type="button"
            onClick={() => setShowSql((shown) => !shown)}
            className="mt-1.5 inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <Code2 className="size-3.5" />
            {showSql ? "Скрыть SQL" : "Показать SQL"}
          </button>
        )}
        {sqlVisible && (
          <pre
            className={cn(
              "mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--muted)] p-2.5 font-mono text-[11px] leading-relaxed",
              step.status === "running" && "assistant-scan",
            )}
          >
            {step.sql}
          </pre>
        )}
        {step.error && <p className="mt-1.5 text-xs text-[var(--destructive)]">{step.error}</p>}
        {live && step.preview && <Preview preview={step.preview} />}
      </div>
    </li>
  );
}

/** Ход работы: шаги по порядку. live — идёт сейчас (SQL раскрыт, видно первые строки результата). */
export function WorkTrace({ steps, live }: { steps: TraceStep[]; live: boolean }) {
  return (
    <ol className="space-y-3" aria-label="Ход работы ассистента">
      {steps.map((step, index) => (
        <TraceItem key={step.id} step={step} last={index === steps.length - 1} live={live} />
      ))}
    </ol>
  );
}
