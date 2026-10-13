"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, ChevronDown, Sparkles, Table as TableIcon, Zap } from "lucide-react";
import { AnswerChart } from "@/components/assistant/answer-chart";
import { DataMapView } from "@/components/assistant/data-map";
import { formatSeconds, WorkTrace } from "@/components/assistant/work-trace";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import {
  formatAssistantCell,
  isNumericCell,
  type AssistantMessage,
  type AssistantStats,
  type AssistantTable,
  type DataMap,
  type TraceStep,
} from "@/lib/assistant";
import { useTypewriter } from "@/lib/use-typewriter";
import { cn, formatCount, pluralRu } from "@/lib/utils";

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end animate-fade-up">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-[var(--primary)] px-4 py-2.5 text-sm text-[var(--primary-foreground)]">
        {text}
      </p>
    </div>
  );
}

function AssistantAvatar() {
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
      <Sparkles className="size-4" />
    </span>
  );
}

function AnswerTable({ table }: { table: AssistantTable }) {
  return (
    <div className="rounded-lg border border-[var(--border)]">
      <div className="max-h-[420px] overflow-auto">
        <Table>
          <THead className="sticky top-0 bg-[var(--card)]">
            <TR>
              {table.columns.map((column, index) => (
                <TH key={`${column}-${index}`} className="whitespace-nowrap border-b border-[var(--border)]">
                  {column}
                </TH>
              ))}
            </TR>
          </THead>
          <TBody>
            {table.rows.map((row, rowIndex) => (
              <TR key={rowIndex}>
                {row.map((cell, index) => (
                  <TD
                    key={index}
                    className={cn("h-10 whitespace-nowrap", isNumericCell(cell) && "text-right tabular-nums")}
                  >
                    {formatAssistantCell(cell, table.columns[index] ?? "")}
                  </TD>
                ))}
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
      {table.truncated && (
        <p className="border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--muted-foreground)]">
          Показаны первые {formatCount(table.rows.length)} строк — уточните вопрос, чтобы сузить выборку.
        </p>
      )}
    </div>
  );
}

function ViewToggle({ chart, onChange }: { chart: boolean; onChange: (chart: boolean) => void }) {
  const option = (active: boolean, label: string, Icon: typeof BarChart3, value: boolean) => (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onChange(value)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors",
        active ? "bg-[var(--card)] text-[var(--foreground)] shadow-xs" : "text-[var(--muted-foreground)]",
      )}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  );
  return (
    <div className="inline-flex rounded-lg bg-[var(--muted)] p-0.5" role="group" aria-label="Вид результата">
      {option(chart, "График", BarChart3, true)}
      {option(!chart, "Таблица", TableIcon, false)}
    </div>
  );
}

function AnswerData({ table }: { table: AssistantTable }) {
  const [showChart, setShowChart] = useState(true);
  if (!table.chart) return <AnswerTable table={table} />;
  return (
    <div className="space-y-2">
      <ViewToggle chart={showChart} onChange={setShowChart} />
      {showChart ? (
        <div className="rounded-lg border border-[var(--border)] p-3 animate-fade-up">
          <AnswerChart table={{ ...table, chart: table.chart }} />
        </div>
      ) : (
        <AnswerTable table={table} />
      )}
    </div>
  );
}

/** «12,4 с · 3 шага модели · 2 SQL · кэш 82 %» — скорость и цена ответа. */
function statsParts(stats: Partial<AssistantStats>): string[] {
  const parts = [];
  if (stats.total_ms !== undefined) parts.push(formatSeconds(stats.total_ms));
  if (stats.model_calls) {
    parts.push(`${stats.model_calls} ${pluralRu(stats.model_calls, ["шаг модели", "шага модели", "шагов модели"])}`);
  }
  if (stats.sql_queries) parts.push(`${stats.sql_queries} SQL`);
  if (stats.input_tokens) parts.push(`кэш ${Math.round(((stats.cached_tokens ?? 0) / stats.input_tokens) * 100)} %`);
  return parts;
}

function statsTitle(stats: Partial<AssistantStats>): string {
  if (!stats.input_tokens) return "";
  const model = stats.model ? `${stats.model}: ` : "";
  return `${model}модель ${formatSeconds(stats.model_ms ?? 0)}, SQL ${formatSeconds(stats.sql_ms ?? 0)}; токены: вход ${formatCount(stats.input_tokens)} (из кэша ${formatCount(stats.cached_tokens ?? 0)}), выход ${formatCount(stats.output_tokens ?? 0)}`;
}

function TraceReplay({ trace, map }: { trace: TraceStep[]; map: DataMap }) {
  return (
    <div className="grid gap-4 rounded-xl border border-[var(--border)] p-3 animate-fade-up @2xl:grid-cols-[minmax(0,1fr)_300px]">
      <WorkTrace steps={trace} live={false} />
      <DataMapView map={map} className="self-start" />
    </div>
  );
}

function AssistantAnswer({ message, fresh }: { message: AssistantMessage; fresh: boolean }) {
  const text = useTypewriter(message.content, fresh && !message.error);
  // Таблица, график и строка скорости появляются, когда текст допечатан.
  const typed = text.length === message.content.length;
  const [showTrace, setShowTrace] = useState(false);
  const stats = statsParts(message.stats);

  return (
    <div className="flex gap-3 animate-fade-up">
      <AssistantAvatar />
      <div className="min-w-0 flex-1 space-y-3 pt-1">
        {message.error ? (
          <p className="flex items-start gap-2 text-sm text-[var(--destructive)]">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            {message.content}
          </p>
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
        )}
        {typed && message.table && <AnswerData table={message.table} />}
        {typed && (stats.length > 0 || message.trace.length > 0) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted-foreground)]">
            {stats.length > 0 && (
              <span className="inline-flex items-center gap-1 tabular-nums" title={statsTitle(message.stats)}>
                <Zap className="size-3.5" />
                {stats.join(" · ")}
              </span>
            )}
            {message.trace.length > 0 && (
              <button
                type="button"
                onClick={() => setShowTrace((shown) => !shown)}
                aria-expanded={showTrace}
                className="inline-flex items-center gap-1 hover:text-[var(--foreground)]"
              >
                Ход работы
                <ChevronDown className={cn("size-3.5 transition-transform", showTrace && "rotate-180")} />
              </button>
            )}
          </div>
        )}
        {showTrace && <TraceReplay trace={message.trace} map={message.data_map} />}
      </div>
    </div>
  );
}

/** fresh — ответ только что пришёл: текст «печатается», остальное появляется следом. */
export function AssistantMessageView({ message, fresh = false }: { message: AssistantMessage; fresh?: boolean }) {
  return message.role === "user" ? (
    <UserBubble text={message.content} />
  ) : (
    <AssistantAnswer message={message} fresh={fresh} />
  );
}

/** Ассистент за работой: шаги по мере прихода, карта данных и секундомер. */
export function WorkingCard({
  question,
  steps,
  map,
  startedAt,
}: {
  question: string | null;
  steps: TraceStep[];
  map: DataMap;
  startedAt: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const running = steps.filter((step) => step.status === "running");
  const active = running.flatMap((step) => step.tables ?? []);
  const sqlCount = steps.filter((step) => step.kind === "sql").length;

  return (
    <>
      {question && <UserBubble text={question} />}
      <div className="assistant-working animate-fade-up" aria-live="polite">
        <div className="rounded-[15px] bg-[var(--card)] p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-2 text-sm font-medium">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--ring)] opacity-60" />
                <span className="relative inline-flex size-2 rounded-full bg-[var(--ring)]" />
              </span>
              Ассистент работает
              <span className="font-normal tabular-nums text-[var(--muted-foreground)]">{seconds} с</span>
            </p>
            <p className="text-xs tabular-nums text-[var(--muted-foreground)]">
              {steps.length} {pluralRu(steps.length, ["шаг", "шага", "шагов"])} · {sqlCount} SQL · {map.tables.length}{" "}
              {pluralRu(map.tables.length, ["таблица", "таблицы", "таблиц"])}
            </p>
          </div>
          <div className="grid gap-4 @2xl:grid-cols-[minmax(0,1fr)_320px]">
            <WorkTrace steps={steps} live />
            <DataMapView map={map} active={active} className="self-start" />
          </div>
        </div>
      </div>
    </>
  );
}
