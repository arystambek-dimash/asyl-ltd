import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BotMessage, TelegramBotStatus } from "@/lib/telegram-bot";
import { pagedState } from "@/test-utils/api";
import { makeBotMessage, makeBotSettings, makeBotStatus } from "@/test-utils/factories";

import TelegramBotPage from "./page";

const mocks = vi.hoisted(() => ({
  permissions: ["bots.view", "bots.manage"] as string[],
  status: null as TelegramBotStatus | null,
  setStatus: vi.fn(),
  paged: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  refresh: vi.fn(),
  refreshStatus: vi.fn(),
  applyItems: vi.fn(),
  polling: [] as { interval: number; active: boolean }[],
  sheet: vi.fn(),
  sheetRow: null as BotMessage | null,
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({
    data: mocks.status,
    error: "",
    loading: false,
    reload: vi.fn(),
    refresh: mocks.refreshStatus,
    setData: mocks.setStatus,
  }),
}));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.paged }));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (_poll: () => Promise<unknown>, interval: number, active = true) => {
    mocks.polling.push({ interval, active });
  },
}));
vi.mock("@/lib/api", () => ({
  api: { post: mocks.post, put: mocks.put },
  apiError: (error: Error) => error.message,
}));
// Лист разбора проверен своими тестами; здесь — с чем журнал его открывает и что делает с ответом.
vi.mock("@/components/loader/rail-report-sheet", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/loader/rail-report-sheet")>()),
  RailReportSheet: (props: { api: string; initialText: string; onApplied: (row: BotMessage) => void }) => {
    mocks.sheet(props);
    return (
      <div role="dialog" aria-label="Провести сообщение">
        <button type="button" onClick={() => props.onApplied(mocks.sheetRow!)}>
          Провести отчёт
        </button>
      </div>
    );
  },
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));

const NOW = Date.now();

const status = (fields: Partial<TelegramBotStatus> = {}) =>
  makeBotStatus({
    polled_at: new Date(NOW - 10_000).toISOString(),
    counts: { review: 1, applied: 3, ignored: 0, all: 4 },
    settings: makeBotSettings({
      allowed_usernames: ["d1maaash"],
      recent_chats: [
        { id: "501", type: "private", title: "Джин-Син", username: "jin_sin", at: "2026-09-28T09:00:00Z" },
        { id: "-100500", type: "supergroup", title: "Отгрузка вагонов", username: "", at: "2026-09-28T08:00:00Z" },
      ],
    }),
    ...fields,
  });

/** Отчёт разобран, но код товара «Д1с» неизвестен — бот уже ответил в чат. */
const message = (fields: Partial<BotMessage> = {}) =>
  makeBotMessage({
    text: "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 12 вагон\nД1с-28087658-68 тн",
    parsed: { client_name: "ООО OSIYO NAV NIHOL", station: "Раустан", wagons: 12, tons: "816" },
    issues: [{ code: "product_unknown", message: "Неизвестный код товара «Д1с»", line: 3, order_id: null }],
    reply: "Принято, на проверке: Неизвестный код товара «Д1с»",
    reply_sent_at: "2026-09-19T09:30:05Z",
    ...fields,
  });

function paged(items: BotMessage[]) {
  return pagedState(items, { refresh: mocks.refresh, applyItems: mocks.applyItems });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.permissions = ["bots.view", "bots.manage"];
  mocks.status = status();
  mocks.polling = [];
  mocks.sheetRow = null;
  mocks.paged.mockReturnValue(paged([message()]));
});

describe("Telegram-бот: журнал", () => {
  it("shows the bot state and the review tab by default, polling quietly", () => {
    render(<TelegramBotPage />);

    expect(screen.getByText("Проводит отчёты")).toBeInTheDocument();
    expect(screen.getByText("Подключён")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "@asyl_bot" })).toHaveAttribute("href", "https://t.me/asyl_bot");
    expect(mocks.paged).toHaveBeenCalledWith("/bots/telegram/messages/?status=review", 50);
    expect(screen.getByRole("tab", { name: /На проверке/ })).toHaveAttribute("aria-selected", "true");
    expect(mocks.polling.at(-1)).toEqual({ interval: 15_000, active: true });
    expect(screen.getByText("ООО OSIYO NAV NIHOL · ст. Раустан · 12 вагонов · 816 т")).toBeInTheDocument();
    // Отправитель с username и чат видны в строке: по ним же ищет поиск журнала.
    expect(screen.getByText(/Джин-Син \(@jin_sin\) · Отгрузка вагонов ·/)).toBeInTheDocument();
  });

  it("switches tabs by server filter", async () => {
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("tab", { name: "Проведено" }));

    expect(mocks.paged).toHaveBeenLastCalledWith("/bots/telegram/messages/?status=applied", 50);
  });

  it("expanded row shows text, reasons and reply; «Провести» opens the report sheet with the message", async () => {
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));

    expect(screen.getByText(/Д1с-28087658-68 тн/)).toBeInTheDocument();
    expect(within(screen.getByRole("status")).getByText("Неизвестный код товара «Д1с»")).toBeInTheDocument();
    expect(screen.getByText("Принято, на проверке: Неизвестный код товара «Д1с»")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Провести/ }));

    expect(mocks.sheet).toHaveBeenCalledWith(
      expect.objectContaining({ api: "/bots/telegram/messages/7", initialText: message().text }),
    );
    // Пока человек решает, опрос не перетирает список.
    expect(mocks.polling.at(-1)?.active).toBe(false);

    const applied = message({ status: "applied", order: 41, issues: [] });
    mocks.sheetRow = applied;
    await userEvent.click(screen.getByRole("button", { name: "Провести отчёт" }));

    const update = mocks.applyItems.mock.calls[0][0] as (items: BotMessage[]) => BotMessage[];
    // Проведённое уходит из вкладки «На проверке».
    expect(update([message()])).toEqual([]);
    // Счётчики шапки перечитываются тихо.
    expect(mocks.refreshStatus).toHaveBeenCalled();
  });

  it("AI draft is what gets conducted", async () => {
    mocks.paged.mockReturnValue(paged([message({ status: "awaiting_confirmation", draft: "черновик отчёта" })]));
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("черновик отчёта")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Провести/ }));

    expect(mocks.sheet).toHaveBeenCalledWith(expect.objectContaining({ initialText: "черновик отчёта" }));
  });

  it("ignore applies the answer; errors stay in the row", async () => {
    const ignored = message({ status: "ignored", resolved_by_name: "Динара", resolved_at: "2026-09-19T10:00:00Z" });
    mocks.post.mockResolvedValueOnce({ data: ignored });
    render(<TelegramBotPage />);
    await userEvent.click(screen.getByRole("button", { expanded: false }));

    await userEvent.click(screen.getByRole("button", { name: /Игнорировать/ }));

    expect(mocks.post).toHaveBeenCalledWith("/bots/telegram/messages/7/ignore/");
    const update = mocks.applyItems.mock.calls[0][0] as (items: BotMessage[]) => BotMessage[];
    expect(update([message()])).toEqual([]);

    mocks.post.mockRejectedValueOnce(new Error("Сообщение уже проведено: заказ №41"));
    await userEvent.click(screen.getByRole("button", { name: /Игнорировать/ }));
    expect(await screen.findByText("Сообщение уже проведено: заказ №41")).toBeInTheDocument();
  });

  it("viewer without bots.manage sees no decisions; conducted row links to its order", async () => {
    mocks.permissions = ["bots.view"];
    mocks.paged.mockReturnValue(paged([message({ status: "applied", order: 41, issues: [] })]));
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));

    expect(screen.queryByRole("button", { name: /Провести/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Игнорировать/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть заказ №41" })).toHaveAttribute(
      "href",
      "/orders/41?back=%2Fmanagement%2Ftelegram-bot",
    );
  });

  it("administrator edits settings and the answer replaces the header", async () => {
    mocks.permissions = ["bots.view", "bots.manage", "sys_permissions.manage"];
    const saved = status({ settings: { ...status().settings, enabled: false } });
    mocks.put.mockResolvedValueOnce({ data: saved });
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    // Дубль вагона — ± дней от даты отчёта, а не «любой повтор за две недели».
    expect(screen.getByLabelText("Дубли вагонов, ± дней")).toHaveValue(3);
    expect(
      screen.getByText("Вагон уже отгружен в пределах ± стольких дней от даты отчёта — на проверку."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Кто пользуется ботом")).toHaveValue("@d1maaash");
    await userEvent.click(screen.getByRole("checkbox", { name: /Бот проводит отчёты/ }));
    // Писавшего боту лично добавляют одним нажатием; группа в подсказки не попадает.
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /Отгрузка вагонов/ })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Допустить @jin_sin" }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(mocks.setStatus).toHaveBeenCalledWith(saved));
    expect(mocks.put).toHaveBeenCalledWith("/bots/telegram/settings/", {
      enabled: false,
      allowed_usernames: ["d1maaash", "jin_sin"],
      show_amounts_in_reply: false,
      duplicate_window_days: 3,
      price_tolerance_pct: "15",
      report_recipients: [],
    });
  });

  it("administrator picks who gets the wagon report — typed or from recent chats", async () => {
    mocks.permissions = ["bots.view", "bots.manage", "sys_permissions.manage"];
    mocks.put.mockResolvedValueOnce({ data: status() });
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    await userEvent.type(screen.getByLabelText("Кому «Отправить отчёт»"), "@Dinara_K");
    await userEvent.click(screen.getByRole("button", { name: "В получатели: @jin_sin" }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalled());
    expect(mocks.put.mock.calls[0][1].report_recipients).toEqual(["dinara_k", "jin_sin"]);
  });

  it("settings tell which recipients have started the bot", async () => {
    mocks.permissions = ["bots.view", "sys_permissions.manage"];
    mocks.status = status({
      settings: makeBotSettings({
        report_recipients: ["dinara_k", "d1maaash"],
        report_recipient_chats: [
          { username: "dinara_k", name: "Динара", ready: true },
          { username: "d1maaash", name: "", ready: false },
        ],
      }),
    });
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));

    expect(screen.getByLabelText("Кому «Отправить отчёт»")).toHaveValue("@dinara_k\n@d1maaash");
    const list = screen.getByRole("list", { name: "Получатели" });
    expect(list).toHaveTextContent("@dinara_k — писал боту, получит отчёт");
    expect(list).toHaveTextContent("@d1maaash — ещё не писал @asyl_bot, попросите нажать /start");
  });

  it("settings errors stay inside the modal", async () => {
    mocks.permissions = ["bots.view", "bots.manage", "sys_permissions.manage"];
    mocks.put.mockRejectedValueOnce(new Error("«x y» — не username Telegram (например, @dinara_k)"));
    render(<TelegramBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("«x y» — не username Telegram (например, @dinara_k)")).toBeInTheDocument();
  });

  it("server switch is explained", () => {
    mocks.status = status({ server_enabled: false });
    render(<TelegramBotPage />);

    expect(screen.getByText("Выключен на сервере")).toBeInTheDocument();
  });
});
