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
  type WhatsAppBotStatus,
} from "./whatsapp-bot";
import { makeBotMessage, makeBotStatus } from "@/test-utils/factories";

const NOW = new Date("2026-09-23T10:00:00Z").getTime();

const status = (fields: Partial<WhatsAppBotStatus> = {}) =>
  makeBotStatus({ polled_at: new Date(NOW - 20_000).toISOString(), ...fields });

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
    const message = makeBotMessage({
      parsed: { client: { name: "ООО OSIYO NAV NIHOL" }, station: "Раустан", wagons: 12, tons: "816" },
    });
    expect(messageSummary(message)).toBe("ООО OSIYO NAV NIHOL · ст. Раустан · 12 вагонов · 816 т");
  });

  it("falls back to the first line of the text", () => {
    expect(messageSummary(makeBotMessage({ text: "\n  Спасибо  \nещё" }))).toBe("Спасибо");
    expect(messageSummary(makeBotMessage({ kind: "deleted", text: "" }))).toBe("Сообщение удалено");
  });
});

describe("decisions", () => {
  it("conducted and deleted messages are final", () => {
    expect(canConduct(makeBotMessage())).toBe(true);
    expect(canConduct(makeBotMessage({ status: "applied" }))).toBe(false);
    expect(canConduct(makeBotMessage({ kind: "deleted" }))).toBe(false);
    // Пропустили по ошибке — провести ещё можно, пропустить второй раз — нечего.
    expect(canConduct(makeBotMessage({ status: "ignored" }))).toBe(true);
    expect(canIgnore(makeBotMessage({ status: "ignored" }))).toBe(false);
    expect(canIgnore(makeBotMessage({ status: "failed" }))).toBe(true);
  });

  it("draft wins over the original text", () => {
    expect(textToConduct(makeBotMessage({ draft: "черновик" }))).toBe("черновик");
    expect(textToConduct(makeBotMessage())).toBe(makeBotMessage().text);
    expect(messageApi(7)).toBe("/bots/whatsapp/messages/7");
  });
});

describe("inTab", () => {
  it("matches the server tabs", () => {
    expect(inTab(makeBotMessage({ status: "awaiting_confirmation" }), "review")).toBe(true);
    expect(inTab(makeBotMessage({ status: "failed" }), "review")).toBe(true);
    expect(inTab(makeBotMessage({ status: "applied" }), "review")).toBe(false);
    expect(inTab(makeBotMessage({ status: "applied" }), "applied")).toBe(true);
    expect(inTab(makeBotMessage({ status: "ignored" }), "ignored")).toBe(true);
    expect(inTab(makeBotMessage({ status: "ignored" }), "all")).toBe(true);
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
