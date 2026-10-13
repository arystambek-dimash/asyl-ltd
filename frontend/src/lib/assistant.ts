import type { AxiosError } from "axios";
import { api, apiError } from "@/lib/api";
import { formatIsoDate, formatMoney } from "@/lib/utils";

export const ASSISTANT_CHATS_URL = "/assistant/chats/";
const ASK_URL = "/assistant/ask/";

export interface AssistantChat {
  id: number;
  title: string;
  updated_at: string;
}

export type Cell = string | number | boolean | null;

interface AssistantChart {
  type: "bar" | "line";
  x: string;
  y: string;
}

export interface AssistantTable {
  columns: string[];
  rows: Cell[][];
  truncated?: boolean;
  chart?: AssistantChart;
}

/** Шаг работы ассистента: раздумье модели, поиск в справочнике, структура таблиц, SQL. */
export interface TraceStep {
  id: number;
  kind: "think" | "search" | "schema" | "sql";
  status: "running" | "done" | "error";
  text: string;
  tables?: string[];
  sql?: string;
  ms?: number;
  row_count?: number;
  found?: number;
  error?: string;
  preview?: { columns: string[]; rows: Cell[][] };
}

/** Затронутые таблицы базы и внешние ключи между ними. */
export interface DataMap {
  tables: string[];
  links: [string, string][];
}

export interface AssistantStats {
  model: string;
  total_ms: number;
  model_calls: number;
  model_ms: number;
  sql_queries: number;
  sql_ms: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
}

export interface AssistantMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  table: AssistantTable | null;
  trace: TraceStep[];
  stats: Partial<AssistantStats>;
  data_map: DataMap;
  error: string;
  created_at: string;
}

export interface AssistantChatDetail extends AssistantChat {
  messages: AssistantMessage[];
}

/** Поток ответа: вопрос записан → шаги и карта данных по мере работы → сохранённый ответ. */
export type AskEvent =
  | { type: "start"; chat: AssistantChat; question: AssistantMessage }
  | { type: "step"; step: TraceStep }
  | ({ type: "map" } & DataMap)
  | { type: "reply"; message: AssistantMessage };

/** Шаг приходит дважды — начат и готов: заменить по id, новый — в конец. */
export function upsertStep(steps: TraceStep[], step: TraceStep): TraceStep[] {
  const index = steps.findIndex((item) => item.id === step.id);
  if (index === -1) return [...steps, step];
  return steps.map((item, position) => (position === index ? step : item));
}

/** «orders_orderitem» → приложение «orders» и модель «orderitem» для подписи узла карты. */
export function tableParts(name: string): { app: string; model: string } {
  const split = name.indexOf("_");
  return split === -1 ? { app: "", model: name } : { app: name.slice(0, split), model: name.slice(split + 1) };
}

const DECIMAL_RE = /^-?\d+(\.\d+)?$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATE_TIME_RE = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})$/;

/** Ячейка таблицы ответа для людей: разряды у чисел, даты ДД.ММ.ГГГГ; номера записей — как есть. */
export function formatAssistantCell(value: Cell, column: string): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "да" : "нет";
  const isId = column === "id" || column.endsWith("_id");
  if (typeof value === "number") return isId ? String(value) : formatMoney(value);
  if (!isId && DECIMAL_RE.test(value)) return formatMoney(value);
  if (ISO_DATE_RE.test(value)) return formatIsoDate(value);
  const dateTime = ISO_DATE_TIME_RE.exec(value);
  if (dateTime) return `${formatIsoDate(dateTime[1])} ${dateTime[2]}`;
  return value;
}

/** Ячейка с числом выравнивается вправо, как суммы в остальных таблицах. */
export function isNumericCell(value: Cell): boolean {
  return typeof value === "number" || (typeof value === "string" && DECIMAL_RE.test(value));
}

async function readText(stream: ReadableStream<Uint8Array>): Promise<string> {
  return new Response(stream).text();
}

/** Ошибка до начала потока (нет права, лимит, нет ключа): тело приходит потоком — распаковать для apiError. */
async function askError(error: unknown): Promise<string> {
  const response = (error as AxiosError).response;
  if (response && response.data instanceof ReadableStream) {
    try {
      const parsed: unknown = JSON.parse(await readText(response.data));
      return apiError({ ...(error as object), response: { ...response, data: parsed } });
    } catch {
      // Не JSON — обычный текст ошибки по статусу.
    }
  }
  return apiError(error);
}

export class AskFailed extends Error {}

/**
 * Задать вопрос: события потока NDJSON по мере работы ассистента.
 * Ответ может идти минуту и дольше — без общего таймаута запросов API.
 */
export async function askAssistant(
  question: string,
  chatId: number | null,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let stream: ReadableStream<Uint8Array>;
  try {
    const response = await api.post<ReadableStream<Uint8Array>>(
      ASK_URL,
      { question, chat: chatId },
      { adapter: "fetch", responseType: "stream", timeout: 0, signal },
    );
    stream = response.data;
  } catch (error) {
    throw new AskFailed(await askError(error));
  }
  const reader = stream.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += value;
    const lines = buffer.split("\n");
    buffer = done ? "" : (lines.pop() ?? "");
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as AskEvent);
    }
    if (done) return;
  }
}
