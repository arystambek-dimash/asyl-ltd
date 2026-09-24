/** «Отправить отчёт» в истории вагонов у грузчика (GET/POST /loader/wagon-report/…). */
import type { LoaderOrder } from "@/lib/loader";
import { composePhone, parsePhone } from "@/lib/phone";
import { formatTime } from "@/lib/utils";

export const WAGON_REPORT_API = "/loader/wagon-report";

/** Как уйдёт отчёт: ботом (очередь на сервере) или ссылкой WhatsApp с этого телефона. */
export type WagonReportDelivery = "bot" | "link";
/**
 * Что с отправленным отчётом: в очереди бота, бот отправляет, отправлен ботом,
 * не ушёл, неизвестно, ушёл ли (WhatsApp не ответил), или отдан ссылкой.
 */
export type WagonReportStatus = "queued" | "sending" | "sent" | "failed" | "unknown" | "link";

export interface WagonReportRecipient {
  /** Как в настройках бота: «Динара». */
  name: string;
  /** В дательном падеже — для «Отправить Динаре». */
  to: string;
  /** Только цифры с кодом страны; пусто — номер не указан. */
  phone: string;
  /** Бот без номера пишет в эту группу. */
  chat_name?: string;
}

/** Ответ «Составить отчёт»: текст в формате владельца, заказы, кому и как. */
export interface WagonReportDraft {
  text: string;
  order_ids: number[];
  recipient: WagonReportRecipient;
  delivery: WagonReportDelivery;
  link?: string;
}

/** Ответ «Отправить»: экран применяет его к строкам истории. */
export interface WagonReportSent {
  status: WagonReportStatus;
  status_label: string;
  sent_at: string;
  order_ids: number[];
  recipient: WagonReportRecipient;
  error: string;
  link?: string;
}

/** Одна отгрузка из истории или вся история с фильтрами экрана. */
export type WagonReportScope = { order: number } | { date_from: string; date_to: string; search: string };

export function composeUrl(scope: WagonReportScope): string {
  const params =
    "order" in scope
      ? { order: String(scope.order) }
      : { date_from: scope.date_from, date_to: scope.date_to, search: scope.search };
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  return `${WAGON_REPORT_API}/compose/?${query}`;
}

/** Ссылка wa.me с текстом — как на сервере: на номер или, без номера, с выбором чата. */
export function whatsappLink(phone: string, text: string): string {
  return `https://wa.me/${phone}?text=${encodeURIComponent(text)}`;
}

/** «+7 701 123-45-67» из цифр номера. */
export function phoneLabel(digits: string): string {
  return digits ? composePhone(parsePhone(`+${digits}`, null)) : "";
}

/** Строка «Кому» в окне отправки. */
export function recipientLine(draft: Pick<WagonReportDraft, "recipient" | "delivery">): string {
  const { recipient, delivery } = draft;
  if (recipient.phone) return `${recipient.to} · ${phoneLabel(recipient.phone)}`;
  if (delivery === "bot") return `${recipient.to} · в группу «${recipient.chat_name || "WhatsApp"}»`;
  return `${recipient.to} · номер не указан — выберите чат в WhatsApp`;
}

/** Ключ нажатия «Отправить»: повтор того же запроса сервер не отправит второй раз. */
export function newSendKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Отметить отправку на показанных строках истории (ответ POST, без перечитывания). */
export function withReportSent(rows: LoaderOrder[], sent: WagonReportSent): LoaderOrder[] {
  const ids = new Set(sent.order_ids);
  return rows.map((row) =>
    ids.has(row.id)
      ? {
          ...row,
          report_sent_at: sent.sent_at,
          report_status: sent.status,
          report_sent_to: sent.recipient.to,
          report_error: sent.error,
        }
      : row,
  );
}

export interface ReportMark {
  tone: "success" | "muted" | "destructive";
  text: string;
}

/** Пометка на карточке истории: «Отправлено Динаре 07:45». */
export function reportMark(order: LoaderOrder): ReportMark | null {
  if (!order.report_sent_at) return null;
  const to = order.report_sent_to ? ` ${order.report_sent_to}` : "";
  const time = formatTime(order.report_sent_at);
  if (order.report_status === "queued") return { tone: "muted", text: `Бот отправит${to} · ${time}` };
  if (order.report_status === "sending") return { tone: "muted", text: `Бот отправляет${to} · ${time}` };
  if (order.report_status === "failed") {
    return { tone: "destructive", text: `Не отправлено${to}: ${order.report_error || "ошибка WhatsApp"}` };
  }
  if (order.report_status === "unknown") {
    // Сообщение могло уйти: повторная отправка без проверки задвоила бы отчёт.
    const reason = order.report_error ? ` (${order.report_error})` : "";
    return {
      tone: "destructive",
      text: `Не подтверждено${to}: проверьте WhatsApp, прежде чем отправлять снова${reason}`,
    };
  }
  return { tone: "success", text: `Отправлено${to} ${time}` };
}
