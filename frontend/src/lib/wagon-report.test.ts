import { describe, expect, it } from "vitest";
import type { LoaderOrder } from "@/lib/loader";
import { formatTime } from "@/lib/utils";
import {
  blockerText,
  newSendKey,
  recipientLabel,
  reportMarks,
  withReportSent,
  type WagonReportDelivery,
  type WagonReportSent,
} from "./wagon-report";

const row = (id: number, fields: Partial<LoaderOrder> = {}): LoaderOrder => ({
  id,
  status: "shipped",
  transport_type: "train",
  truck_number: "12345678",
  planned_on: "2026-09-24",
  client_name: "ООО OSIYO NAV NIHOL",
  items: [],
  bags: 8160,
  total_kg: "408000.00",
  shipped_at: "2026-09-24T07:30:00+05:00",
  trailer_number: "",
  transport_suggestions: [],
  transport_locked: false,
  client_country: "KZ",
  can_rollback: true,
  rail_station: "",
  wagons: [],
  report_sent_at: null,
  report_deliveries: [],
  ...fields,
});

const SENT_AT = "2026-09-24T07:45:00+05:00";

const delivery = (to: string, status: WagonReportDelivery["status"], error = ""): WagonReportDelivery => ({
  to,
  status,
  status_label: status,
  error,
});

describe("wagon report", () => {
  it("получатель — имя из Telegram и username, пока не писал боту — только username", () => {
    expect(recipientLabel({ username: "dinara_k", name: "Динара", ready: true })).toBe("Динара (@dinara_k)");
    expect(recipientLabel({ username: "d1maaash", name: "", ready: false })).toBe("@d1maaash");
  });

  it("почему отправить нельзя", () => {
    expect(blockerText({ reason: "" })).toBe("");
    expect(blockerText({ reason: "bot_off" })).toMatch(/^Telegram-бот сейчас не работает/);
    expect(blockerText({ reason: "no_recipients" })).toMatch(/Не выбрано, кому отправлять/);
    expect(blockerText({ reason: "not_started" })).toMatch(/не написал боту \/start/);
  });

  it("ключ нажатия подходит серверу", () => {
    expect(newSendKey()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(newSendKey()).not.toBe(newSendKey());
  });

  it("отметка отправки ложится на строки отчёта, остальные не трогает", () => {
    const sent: WagonReportSent = { sent_at: SENT_AT, order_ids: [366], deliveries: [delivery("@dinara_k", "queued")] };

    const [marked, other] = withReportSent([row(366), row(367)], sent);

    expect(marked).toMatchObject({ report_sent_at: SENT_AT, report_deliveries: sent.deliveries });
    expect(other.report_sent_at).toBeNull();
  });

  it("пометки на карточке — по строке на исход отправки", () => {
    const time = formatTime(SENT_AT);
    const sentRow = (...deliveries: WagonReportDelivery[]) =>
      row(366, { report_sent_at: SENT_AT, report_deliveries: deliveries });

    expect(reportMarks(row(366))).toEqual([]);
    expect(reportMarks(sentRow(delivery("@dinara_k", "sent"), delivery("@d1maaash", "sent")))).toEqual([
      { tone: "success", text: `Отправлено @dinara_k, @d1maaash · ${time}` },
    ]);
    expect(
      reportMarks(
        sentRow(
          delivery("@dinara_k", "sent"),
          delivery("@jin_sin", "queued"),
          delivery("@d1maaash", "failed", "Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user"),
        ),
      ),
    ).toEqual([
      { tone: "success", text: `Отправлено @dinara_k · ${time}` },
      { tone: "muted", text: `Бот отправит @jin_sin · ${time}` },
      {
        tone: "destructive",
        text: "Не отправлено @d1maaash: Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user",
      },
    ]);
    // Ответа Telegram нет — могло уйти: сначала проверить чат, потом отправлять снова.
    expect(reportMarks(sentRow(delivery("@dinara_k", "unknown", "нет связи (TimeoutError)")))).toEqual([
      {
        tone: "destructive",
        text: "Не подтверждено @dinara_k: проверьте Telegram, прежде чем отправлять снова (нет связи (TimeoutError))",
      },
    ]);
    // Отметка без доставок (отправляли, пока шёл откат версии) — факт отправки всё равно виден.
    expect(reportMarks(row(366, { report_sent_at: SENT_AT }))).toEqual([
      { tone: "muted", text: `Отчёт отправлен · ${time}` },
    ]);
    // История: до 29.09 отчёт отправляли ссылкой с телефона.
    expect(reportMarks(sentRow(delivery("Динара", "link")))).toEqual([
      { tone: "success", text: `Отправлено ссылкой Динара · ${time}` },
    ]);
  });
});
