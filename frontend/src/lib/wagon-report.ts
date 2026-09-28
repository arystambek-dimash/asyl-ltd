/** «Отправить отчёт» в истории вагонов у грузчика (GET/POST /loader/wagon-report/…). */
import type { LoaderOrder } from "@/lib/loader";
import { formatTime } from "@/lib/utils";

export const WAGON_REPORT_API = "/loader/wagon-report";

/** Почему «Отправить» недоступно: бот не работает, получатели не выбраны, никто из них не писал боту. */
type WagonReportBlocker = "" | "bot_off" | "no_recipients" | "not_started";
/**
 * Что с отчётом у получателя: в очереди бота, бот отправляет, отправлено,
 * не ушло, неизвестно, ушло ли (Telegram не ответил), или (история) ссылкой.
 */
type WagonReportStatus = "queued" | "sending" | "sent" | "failed" | "unknown" | "link";

/** Получатель из настроек бота: может ли бот ему написать (писал ли он боту /start). */
export interface WagonReportRecipient {
  username: string;
  /** Как человек подписан в Telegram; пусто — ещё не писал боту. */
  name: string;
  ready: boolean;
}

/** Доставка отчёта одному получателю. */
export interface WagonReportDelivery {
  /** «@dinara_k». */
  to: string;
  status: WagonReportStatus;
  status_label: string;
  error: string;
}

/** Ответ «Составить отчёт»: текст в формате владельца, заказы, кому и можно ли отправить. */
export interface WagonReportDraft {
  text: string;
  order_ids: number[];
  recipients: WagonReportRecipient[];
  can_send: boolean;
  reason: WagonReportBlocker;
}

/** Ответ «Отправить»: экран применяет его к строкам истории. */
export interface WagonReportSent {
  sent_at: string;
  order_ids: number[];
  deliveries: WagonReportDelivery[];
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

/** «Динара (@dinara_k)» или «@dinara_k», пока человек не писал боту. */
export function recipientLabel(recipient: WagonReportRecipient): string {
  return recipient.name ? `${recipient.name} (@${recipient.username})` : `@${recipient.username}`;
}

const BLOCKERS: Record<Exclude<WagonReportBlocker, "">, string> = {
  bot_off: "Telegram-бот сейчас не работает — отправить нельзя. Скопируйте текст и отправьте сами.",
  no_recipients: "Не выбрано, кому отправлять отчёт: администратор выбирает получателей в «Telegram-бот → Настройки».",
  not_started: "Никто из получателей ещё не написал боту /start — бот не может написать первым.",
};

/** Почему «Отправить» недоступно — строкой в окне; пусто — можно отправлять. */
export function blockerText(draft: Pick<WagonReportDraft, "reason">): string {
  return draft.reason ? BLOCKERS[draft.reason] : "";
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
    ids.has(row.id) ? { ...row, report_sent_at: sent.sent_at, report_deliveries: sent.deliveries } : row,
  );
}

interface ReportMark {
  tone: "success" | "muted" | "destructive";
  text: string;
}

const MARK_ORDER: WagonReportStatus[] = ["sent", "link", "queued", "sending", "failed", "unknown"];

/**
 * Пометки на карточке истории — по строке на исход: «Отправлено @a, @b · 07:45»,
 * «Бот отправит @c», «Не отправлено @d: причина».
 */
export function reportMarks(order: LoaderOrder): ReportMark[] {
  if (!order.report_sent_at) return [];
  const time = formatTime(order.report_sent_at);
  const deliveries = order.report_deliveries ?? [];
  // Отметка есть, а доставок нет — отчёт отправляли, пока шёл откат версии: сам факт виден.
  if (deliveries.length === 0) return [{ tone: "muted", text: `Отчёт отправлен · ${time}` }];
  const byStatus = new Map<WagonReportStatus, WagonReportDelivery[]>();
  for (const delivery of deliveries) {
    byStatus.set(delivery.status, [...(byStatus.get(delivery.status) ?? []), delivery]);
  }
  const marks: ReportMark[] = [];
  for (const status of MARK_ORDER) {
    const group = byStatus.get(status);
    if (!group) continue;
    const to = group.map((delivery) => delivery.to).join(", ");
    if (status === "sent") marks.push({ tone: "success", text: `Отправлено ${to} · ${time}` });
    if (status === "link") marks.push({ tone: "success", text: `Отправлено ссылкой ${to} · ${time}` });
    if (status === "queued") marks.push({ tone: "muted", text: `Бот отправит ${to} · ${time}` });
    if (status === "sending") marks.push({ tone: "muted", text: `Бот отправляет ${to} · ${time}` });
    for (const delivery of status === "failed" ? group : []) {
      marks.push({
        tone: "destructive",
        text: `Не отправлено ${delivery.to}: ${delivery.error || "ошибка Telegram"}`,
      });
    }
    for (const delivery of status === "unknown" ? group : []) {
      // Сообщение могло уйти: повторная отправка без проверки задвоила бы отчёт.
      const reason = delivery.error ? ` (${delivery.error})` : "";
      marks.push({
        tone: "destructive",
        text: `Не подтверждено ${delivery.to}: проверьте Telegram, прежде чем отправлять снова${reason}`,
      });
    }
  }
  return marks;
}
