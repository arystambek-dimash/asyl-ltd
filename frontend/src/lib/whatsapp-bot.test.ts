import { describe, expect, it } from "vitest";
import {
  agoLabel,
  botHealth,
  canConduct,
  canIgnore,
  inTab,
  messageApi,
  messageSummary,
  parseIdList,
  textToConduct,
  type BotMessage,
  type WhatsAppBotStatus,
} from "./whatsapp-bot";

const NOW = new Date("2026-09-23T10:00:00Z").getTime();

function status(fields: Partial<WhatsAppBotStatus> = {}): WhatsAppBotStatus {
  return {
    server_enabled: true,
    runtime_status: "running",
    runtime_error: "",
    polled_at: new Date(NOW - 20_000).toISOString(),
    instance_state: "authorized",
    instance_state_at: null,
    counts: { review: 0, applied: 0, ignored: 0, all: 0 },
    settings: {
      enabled: true,
      allowed_chat_ids: [],
      allowed_sender_ids: [],
      show_amounts_in_reply: false,
      duplicate_window_days: 3,
      price_tolerance_pct: "15.00",
      report_recipient_name: "Динара",
      report_recipient_phone: "",
      updated_at: "2026-09-23T09:00:00Z",
      seen_chats: [],
    },
    can_manage: true,
    can_configure: true,
    ...fields,
  };
}

function botMessage(fields: Partial<BotMessage> = {}): BotMessage {
  return {
    id: 7,
    kind: "message",
    status: "needs_review",
    chat_id: "120363043968066561@g.us",
    chat_name: "Отгрузка вагонов",
    sender_id: "998901112233@c.us",
    sender_name: "Джин-Син",
    text: "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 12 вагон",
    sent_at: "2026-09-19T09:30:00Z",
    received_at: "2026-09-19T09:30:02Z",
    parsed: {},
    issues: [],
    draft: "",
    order: null,
    original: null,
    reply: "",
    reply_sent_at: null,
    reply_attempts: 0,
    attempts: 0,
    error: "",
    resolved_by_name: "",
    resolved_at: null,
    ...fields,
  };
}

describe("botHealth", () => {
  it("running bot shows the last poll", () => {
    expect(botHealth(status(), NOW)).toEqual({ tone: "success", label: "Проводит отчёты", detail: "Опрос 20 с назад" });
  });

  it("server flag comes first", () => {
    expect(botHealth(status({ server_enabled: false, polled_at: null }), NOW).label).toBe("Выключен на сервере");
  });

  it("silent process is not responding", () => {
    expect(botHealth(status({ polled_at: null }), NOW).label).toBe("Бот не отвечает");
    const stale = botHealth(status({ polled_at: new Date(NOW - 5 * 60_000).toISOString() }), NOW);
    expect(stale).toMatchObject({ tone: "destructive", detail: "Последний опрос 5 мин назад" });
  });

  it("switch in settings and provider outage", () => {
    const off = status({ settings: { ...status().settings, enabled: false } });
    expect(botHealth(off, NOW).label).toBe("Выключен в настройках");
    const degraded = botHealth(status({ runtime_status: "degraded", runtime_error: "HTTP 401" }), NOW);
    expect(degraded).toMatchObject({ tone: "warning", detail: "HTTP 401" });
  });
});

describe("agoLabel", () => {
  it("seconds, minutes, hours", () => {
    expect(agoLabel(NOW - 5_000, NOW)).toBe("5 с назад");
    expect(agoLabel(NOW - 4 * 60_000, NOW)).toBe("4 мин назад");
    expect(agoLabel(NOW - 2 * 3_600_000, NOW)).toBe("2 ч назад");
  });
});

describe("messageSummary", () => {
  it("parsed report reads as client, station, wagons and tons", () => {
    const message = botMessage({
      parsed: { client: { id: 1, name: "ООО OSIYO NAV NIHOL" }, station: "Раустан", wagons: 12, tons: "816" },
    });
    expect(messageSummary(message)).toBe("ООО OSIYO NAV NIHOL · ст. Раустан · 12 вагонов · 816 т");
  });

  it("falls back to the first line of the text", () => {
    expect(messageSummary(botMessage({ text: "\n  Спасибо  \nещё" }))).toBe("Спасибо");
    expect(messageSummary(botMessage({ kind: "deleted", text: "" }))).toBe("Сообщение удалено");
  });
});

describe("decisions", () => {
  it("conducted and deleted messages are final", () => {
    expect(canConduct(botMessage())).toBe(true);
    expect(canConduct(botMessage({ status: "applied" }))).toBe(false);
    expect(canConduct(botMessage({ kind: "deleted" }))).toBe(false);
    // Пропустили по ошибке — провести ещё можно, пропустить второй раз — нечего.
    expect(canConduct(botMessage({ status: "ignored" }))).toBe(true);
    expect(canIgnore(botMessage({ status: "ignored" }))).toBe(false);
    expect(canIgnore(botMessage({ status: "failed" }))).toBe(true);
  });

  it("draft wins over the original text", () => {
    expect(textToConduct(botMessage({ draft: "черновик" }))).toBe("черновик");
    expect(textToConduct(botMessage())).toBe(botMessage().text);
    expect(messageApi(7)).toBe("/bots/whatsapp/messages/7");
  });
});

describe("inTab", () => {
  it("matches the server tabs", () => {
    expect(inTab(botMessage({ status: "awaiting_confirmation" }), "review")).toBe(true);
    expect(inTab(botMessage({ status: "failed" }), "review")).toBe(true);
    expect(inTab(botMessage({ status: "applied" }), "review")).toBe(false);
    expect(inTab(botMessage({ status: "applied" }), "applied")).toBe(true);
    expect(inTab(botMessage({ status: "ignored" }), "ignored")).toBe(true);
    expect(inTab(botMessage({ status: "ignored" }), "all")).toBe(true);
  });
});

describe("parseIdList", () => {
  it("one per line, commas too, no blanks or repeats", () => {
    expect(parseIdList(" 120363@g.us \n\n+998 90 111 22 33, 120363@g.us;77011234567@c.us")).toEqual([
      "120363@g.us",
      "+998 90 111 22 33",
      "77011234567@c.us",
    ]);
  });
});
