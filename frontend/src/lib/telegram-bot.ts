/** Журнал Telegram-бота отчётов о вагонах (GET/POST /bots/telegram/…). */
import type { BadgeTone } from "@/lib/constants";
import type { RailIssue } from "@/lib/rail-report";
import { formatMoney } from "@/lib/utils";
import { wagonsWord } from "@/lib/wagons";

export const TELEGRAM_BOT_API = "/bots/telegram";

export type BotMessageStatus =
  "received" | "applied" | "needs_review" | "awaiting_confirmation" | "rejected" | "ignored" | "failed";

export type BotMessageKind = "message" | "edited" | "deleted";

/** Итог разбора, записанный ботом: без денег (их показывает предпросмотр по правам). */
export interface BotMessageParsed {
  client_name?: string;
  client?: { name: string } | null;
  station?: string;
  wagons?: number;
  tons?: string;
}

export interface BotMessage {
  id: number;
  /** telegram; green_api — история прежнего WhatsApp-бота. */
  provider: string;
  kind: BotMessageKind;
  status: BotMessageStatus;
  chat_name: string;
  sender_id: string;
  sender_name: string;
  /** Username отправителя в Telegram без «@»; по нему — доступ к боту. */
  sender_username: string;
  text: string;
  sent_at: string | null;
  received_at: string;
  parsed: BotMessageParsed;
  issues: RailIssue[];
  /** Черновик ИИ для сообщения не по формату — проводит только человек. */
  draft: string;
  order: number | null;
  original: number | null;
  reply: string;
  reply_sent_at: string | null;
  reply_attempts: number;
  error: string;
  resolved_by_name: string;
  resolved_at: string | null;
}

/** Чат, откуда писали боту: личный (с username собеседника) или группа. */
interface BotChat {
  id: string;
  type: string;
  title: string;
  username: string;
  at: string;
}

export interface TelegramBotSettings {
  enabled: boolean;
  /** Кто пользуется ботом: username в Telegram без «@». */
  allowed_usernames: string[];
  show_amounts_in_reply: boolean;
  /** Дубль вагона: тот же номер отгружен в пределах ± стольких дней от даты отчёта (по умолчанию 3). */
  duplicate_window_days: number;
  price_tolerance_pct: string;
  /** «Отправить отчёт» в истории грузчика: кому (по умолчанию «Динара») и её username без «@». */
  report_recipient_name: string;
  report_recipient_username: string;
  /** Получатель уже написал боту /start — бот может ему отправить. */
  report_recipient_started: boolean;
  updated_at: string;
  /** Недавно писали боту — добавить username в допущенные, не набирая его. */
  recent_chats: BotChat[];
}

export type BotTab = "review" | "applied" | "ignored" | "all";

export interface TelegramBotStatus {
  /** TELEGRAM_BOT_ENABLED на сервере. */
  server_enabled: boolean;
  runtime_status: "" | "running" | "degraded" | "disabled";
  runtime_error: string;
  polled_at: string | null;
  /** getMe: authorized — токен рабочий, unauthorized — Telegram его отверг. */
  bot_state: string;
  /** Username бота без «@» — по нему бота находят в Telegram. */
  bot_username: string;
  counts: Record<BotTab, number>;
  settings: TelegramBotSettings;
}

export const MESSAGE_STATUS: Record<BotMessageStatus, { label: string; tone: BadgeTone }> = {
  received: { label: "Получено", tone: "muted" },
  applied: { label: "Проведено", tone: "success" },
  needs_review: { label: "На проверке", tone: "warning" },
  awaiting_confirmation: { label: "Черновик ИИ", tone: "primary" },
  rejected: { label: "Отклонено", tone: "destructive" },
  ignored: { label: "Пропущено", tone: "muted" },
  failed: { label: "Ошибка", tone: "destructive" },
};

export const MESSAGE_KIND: Record<Exclude<BotMessageKind, "message">, string> = {
  edited: "Изменено",
  deleted: "Удалено",
};

/** Состояние токена бота (getMe). */
export const BOT_STATES: Record<string, string> = {
  authorized: "Подключён",
  unauthorized: "Telegram отверг токен — проверьте TELEGRAM_BOT_TOKEN",
};

/** Бот пишет состояние раз в полминуты; дольше трёх минут тишины — процесс не отвечает. */
const BOT_STALE_MS = 3 * 60 * 1000;

interface BotHealth {
  tone: BadgeTone;
  label: string;
  detail: string;
}

/** Что сейчас с ботом — одной строкой для шапки журнала. */
export function botHealth(status: TelegramBotStatus, now: number = Date.now()): BotHealth {
  if (!status.server_enabled) {
    return {
      tone: "muted",
      label: "Выключен на сервере",
      detail: "TELEGRAM_BOT_ENABLED=1 и TELEGRAM_BOT_TOKEN в .env включают бота",
    };
  }
  const polled = status.polled_at ? new Date(status.polled_at).getTime() : null;
  if (polled === null || now - polled > BOT_STALE_MS) {
    return {
      tone: "destructive",
      label: "Бот не отвечает",
      detail: polled === null ? "Процесс ещё ни разу не выходил на связь" : `Последний опрос ${agoLabel(polled, now)}`,
    };
  }
  if (!status.settings.enabled || status.runtime_status === "disabled") {
    return {
      tone: "muted",
      label: "Выключен в настройках",
      detail: "Сообщения ждут в Telegram до суток",
    };
  }
  if (status.runtime_status === "degraded") {
    return { tone: "warning", label: "Нет связи с Telegram", detail: status.runtime_error || "Повторяем попытки" };
  }
  return { tone: "success", label: "Проводит отчёты", detail: `Опрос ${agoLabel(polled, now)}` };
}

/** «12 с назад», «4 мин назад», «2 ч назад». */
export function agoLabel(at: number, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 60) return `${seconds} с назад`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.round(minutes / 60)} ч назад`;
}

/** «ООО OSIYO · ст. Раустан · 12 вагонов · 816 т» или первая строка сообщения. */
export function messageSummary(message: BotMessage): string {
  const parsed = message.parsed ?? {};
  const parts: string[] = [];
  const client = parsed.client?.name || parsed.client_name;
  if (client) parts.push(client);
  if (parsed.station) parts.push(`ст. ${parsed.station}`);
  if (parsed.wagons) parts.push(`${parsed.wagons} ${wagonsWord(parsed.wagons)}`);
  if (parsed.tons && parsed.tons !== "0") parts.push(`${formatMoney(parsed.tons)} т`);
  if (parts.length > 0) return parts.join(" · ");
  const firstLine = message.text.split("\n").find((line) => line.trim()) ?? "";
  return firstLine.trim() || (message.kind === "deleted" ? "Сообщение удалено" : "Пустое сообщение");
}

/** Текст для «Провести»: черновик ИИ, если он есть, иначе как пришло. */
export function textToConduct(message: BotMessage): string {
  return message.draft || message.text;
}

/** Разбор сообщения — тот же лист, что «Отгрузить по отчёту» у грузчика, со своими адресами. */
export function messageApi(id: number): string {
  return `${TELEGRAM_BOT_API}/messages/${id}`;
}

/** «Провести»: проведённое и удалённое — уже нет; пропущенное по ошибке — можно. */
export function canConduct(message: BotMessage): boolean {
  return message.status !== "applied" && message.kind !== "deleted";
}

/** «Игнорировать»: то, что ещё ждёт решения. */
export function canIgnore(message: BotMessage): boolean {
  return message.status !== "applied" && message.status !== "ignored";
}

const REVIEW_STATUSES: readonly BotMessageStatus[] = ["needs_review", "awaiting_confirmation", "failed", "rejected"];

/** Попадает ли сообщение во вкладку (как фильтр ?status= на сервере) — после «Провести»/«Игнорировать». */
export function inTab(message: BotMessage, tab: BotTab): boolean {
  if (tab === "all") return true;
  if (tab === "review") return REVIEW_STATUSES.includes(message.status);
  return message.status === tab;
}

/** «@Dinara_K» или ссылка t.me → «dinara_k»: username сравнивается без регистра и «@». */
export function normalizeUsername(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/^(https?:\/\/)?t\.me\//, "")
    .replace(/^@/, "");
}

/** Поле «по одному в строке» → username без «@», пустых и повторов. */
export function parseUsernames(text: string): string[] {
  return Array.from(
    new Set(
      text
        .split(/[\s,;]+/)
        .map(normalizeUsername)
        .filter(Boolean),
    ),
  );
}

/** «Джин-Син (@jin_sin)» — кто прислал сообщение. */
export function senderLabel(message: BotMessage): string {
  const name = message.sender_name || message.sender_id;
  return message.sender_username ? `${name} (@${message.sender_username})` : name;
}
