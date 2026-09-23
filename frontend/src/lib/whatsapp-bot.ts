/** Журнал WhatsApp-бота отчётов о вагонах (GET/POST /bots/whatsapp/…). */
import type { RailIssue } from "@/lib/rail-report";
import { formatMoney } from "@/lib/utils";
import { wagonsWord } from "@/lib/wagons";

export const WHATSAPP_BOT_API = "/bots/whatsapp";

export type BotMessageStatus =
  "received" | "parsed" | "applied" | "needs_review" | "awaiting_confirmation" | "rejected" | "ignored" | "failed";

export type BotMessageKind = "message" | "edited" | "deleted";

/** Итог разбора, записанный ботом: без денег (их показывает предпросмотр по правам). */
export interface BotMessageParsed {
  day?: string | null;
  country?: string;
  client_name?: string;
  client?: { id: number; name: string } | null;
  station?: string;
  wagons?: number;
  tons?: string;
  bags?: number | null;
}

export interface BotMessage {
  id: number;
  kind: BotMessageKind;
  status: BotMessageStatus;
  chat_id: string;
  chat_name: string;
  sender_id: string;
  sender_name: string;
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
  attempts: number;
  error: string;
  resolved_by_name: string;
  resolved_at: string | null;
}

export interface WhatsAppBotSettings {
  enabled: boolean;
  allowed_chat_ids: string[];
  allowed_sender_ids: string[];
  show_amounts_in_reply: boolean;
  /** Дубль вагона: тот же номер отгружен в пределах ± стольких дней от даты отчёта (по умолчанию 3). */
  duplicate_window_days: number;
  price_tolerance_pct: string;
  updated_at: string;
  /** Недавние чаты бота — выбрать группу, не зная её идентификатора. */
  seen_chats: { id: string; name: string; at: string | null }[];
}

export type BotTab = "review" | "applied" | "ignored" | "all";

export interface WhatsAppBotStatus {
  /** WHATSAPP_BOT_ENABLED на сервере. */
  server_enabled: boolean;
  runtime_status: "" | "running" | "degraded" | "disabled";
  runtime_error: string;
  polled_at: string | null;
  instance_state: string;
  instance_state_at: string | null;
  counts: Record<BotTab, number>;
  settings: WhatsAppBotSettings;
  can_manage: boolean;
  can_configure: boolean;
}

type Tone = "muted" | "primary" | "success" | "warning" | "destructive";

export const MESSAGE_STATUS: Record<BotMessageStatus, { label: string; tone: Tone }> = {
  received: { label: "Получено", tone: "muted" },
  parsed: { label: "Разбирается", tone: "muted" },
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

/** Состояния номера в Green-API. */
export const INSTANCE_STATES: Record<string, string> = {
  authorized: "Подключён",
  notAuthorized: "Не авторизован — отсканируйте QR-код в кабинете Green-API",
  blocked: "Заблокирован WhatsApp",
  sleepMode: "Спящий режим — телефон офлайн",
  starting: "Запускается",
  yellowCard: "Ограничен WhatsApp (жёлтая карточка)",
};

/** Бот пишет состояние раз в полминуты; дольше трёх минут тишины — процесс не отвечает. */
export const BOT_STALE_MS = 3 * 60 * 1000;

export interface BotHealth {
  tone: Tone;
  label: string;
  detail: string;
}

/** Что сейчас с ботом — одной строкой для шапки журнала. */
export function botHealth(status: WhatsAppBotStatus, now: number = Date.now()): BotHealth {
  if (!status.server_enabled) {
    return { tone: "muted", label: "Выключен на сервере", detail: "WHATSAPP_BOT_ENABLED=1 в .env включает бота" };
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
      detail: "Сообщения копятся у Green-API до суток",
    };
  }
  if (status.runtime_status === "degraded") {
    return { tone: "warning", label: "Нет связи с WhatsApp", detail: status.runtime_error || "Повторяем попытки" };
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

/** Разбор сообщения — тот же лист, что «Вставить отчёт» у грузчика, со своими адресами. */
export function messageApi(id: number): string {
  return `${WHATSAPP_BOT_API}/messages/${id}`;
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

/** Поле «по одному в строке» → список без пустых и повторов. */
export function parseIdList(text: string): string[] {
  return Array.from(
    new Set(
      text
        .split(/[\n,;]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  );
}
