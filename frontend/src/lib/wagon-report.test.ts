import { describe, expect, it } from "vitest";
import type { LoaderOrder } from "@/lib/loader";
import { formatTime } from "@/lib/utils";
import {
  composeUrl,
  newSendKey,
  recipientLine,
  reportMark,
  withReportSent,
  type WagonReportSent,
} from "./wagon-report";

const row = (id: number, fields: Partial<LoaderOrder> = {}): LoaderOrder => ({
  id,
  status: "shipped",
  transport_type: "train",
  truck_number: "12345678",
  currency: "USD",
  planned_on: "2026-09-24",
  client_name: "ООО OSIYO NAV NIHOL",
  items: [],
  bags: 8160,
  total_kg: "408000.00",
  total_amount: "61200.00",
  shipped_at: "2026-09-24T07:30:00+05:00",
  trailer_number: "",
  transport_suggestions: [],
  transport_locked: false,
  client_country: "KZ",
  payment_status: "unpaid",
  remaining_amount: "61200.00",
  can_rollback: true,
  rail_station: "",
  wagons: [],
  report_sent_at: null,
  report_sent_to: "",
  report_status: "",
  report_error: "",
  ...fields,
});

const SENT_AT = "2026-09-24T07:45:00+05:00";
const recipient = { name: "Динара", to: "Динаре", phone: "" };

describe("wagon report", () => {
  it("составляет по одной отгрузке или по фильтру истории", () => {
    expect(composeUrl({ order: 366 })).toBe("/loader/wagon-report/compose/?order=366");
    expect(composeUrl({ date_from: "2026-09-18", date_to: "2026-09-24", search: "OSIYO" })).toBe(
      "/loader/wagon-report/compose/?date_from=2026-09-18&date_to=2026-09-24&search=OSIYO",
    );
  });

  it("кому: номер, группа бота или выбор чата", () => {
    expect(recipientLine({ recipient: { ...recipient, phone: "77011234567" }, delivery: "link" })).toMatch(
      /^Динаре · \+7 .*701.*123.*45.*67$/,
    );
    expect(recipientLine({ recipient: { ...recipient, chat_name: "Отгрузка вагонов" }, delivery: "bot" })).toBe(
      "Динаре · в группу «Отгрузка вагонов»",
    );
    expect(recipientLine({ recipient, delivery: "link" })).toBe("Динаре · номер не указан — выберите чат в WhatsApp");
  });

  it("ключ нажатия подходит серверу", () => {
    expect(newSendKey()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(newSendKey()).not.toBe(newSendKey());
  });

  it("отметка отправки ложится на строки отчёта, остальные не трогает", () => {
    const sent: WagonReportSent = {
      status: "link",
      status_label: "Ссылкой WhatsApp",
      sent_at: SENT_AT,
      order_ids: [366],
      recipient,
      error: "",
    };

    const [marked, other] = withReportSent([row(366), row(367)], sent);

    expect(marked).toMatchObject({ report_sent_at: SENT_AT, report_status: "link", report_sent_to: "Динаре" });
    expect(other.report_sent_at).toBeNull();
  });

  it("пометка на карточке: отправлено, в очереди или не отправлено", () => {
    const time = formatTime(SENT_AT);
    const sentRow = (status: LoaderOrder["report_status"], error = "") =>
      row(366, { report_sent_at: SENT_AT, report_sent_to: "Динаре", report_status: status, report_error: error });

    expect(reportMark(row(366))).toBeNull();
    expect(reportMark(sentRow("link"))).toEqual({ tone: "success", text: `Отправлено Динаре ${time}` });
    expect(reportMark(sentRow("sent"))).toEqual({ tone: "success", text: `Отправлено Динаре ${time}` });
    expect(reportMark(sentRow("queued"))).toEqual({ tone: "muted", text: `Бот отправит Динаре · ${time}` });
    expect(reportMark(sentRow("failed", "Green-API sendMessage: HTTP 500"))).toEqual({
      tone: "destructive",
      text: "Не отправлено Динаре: Green-API sendMessage: HTTP 500",
    });
    expect(reportMark(sentRow("sending"))).toEqual({ tone: "muted", text: `Бот отправляет Динаре · ${time}` });
    // Ответа WhatsApp нет — могло уйти: сначала проверить чат, потом отправлять снова.
    expect(reportMark(sentRow("unknown", "Green-API sendMessage: нет связи (TimeoutError)"))).toEqual({
      tone: "destructive",
      text: "Не подтверждено Динаре: проверьте WhatsApp, прежде чем отправлять снова (Green-API sendMessage: нет связи (TimeoutError))",
    });
  });
});
