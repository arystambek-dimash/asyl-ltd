import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BotMessage, WhatsAppBotStatus } from "@/lib/whatsapp-bot";

import WhatsAppBotPage from "./page";

const mocks = vi.hoisted(() => ({
  permissions: ["bots.view", "bots.manage"] as string[],
  status: null as WhatsAppBotStatus | null,
  setStatus: vi.fn(),
  paged: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
  put: vi.fn(),
  refresh: vi.fn(),
  applyItems: vi.fn(),
  polling: [] as { interval: number; active: boolean }[],
  sheet: vi.fn(),
  sheetRow: null as BotMessage | null,
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: mocks.status, error: "", loading: false, reload: vi.fn(), setData: mocks.setStatus }),
}));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.paged }));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (_poll: () => Promise<unknown>, interval: number, active = true) => {
    mocks.polling.push({ interval, active });
  },
}));
vi.mock("@/lib/api", () => ({
  api: { post: mocks.post, get: mocks.get, put: mocks.put },
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
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, actions }: { children: ReactNode; actions?: ReactNode }) => (
    <main>
      {actions}
      {children}
    </main>
  ),
}));

const NOW = Date.now();

function status(fields: Partial<WhatsAppBotStatus> = {}): WhatsAppBotStatus {
  return {
    server_enabled: true,
    runtime_status: "running",
    runtime_error: "",
    polled_at: new Date(NOW - 10_000).toISOString(),
    instance_state: "authorized",
    instance_state_at: null,
    counts: { review: 1, applied: 3, ignored: 0, all: 4 },
    settings: {
      enabled: true,
      allowed_chat_ids: ["120363043968066561@g.us"],
      allowed_sender_ids: ["998901112233@c.us"],
      show_amounts_in_reply: false,
      duplicate_window_days: 3,
      price_tolerance_pct: "15.00",
      updated_at: "2026-09-23T09:00:00Z",
      seen_chats: [{ id: "120363000000000001@g.us", name: "Склад", at: null }],
    },
    can_manage: true,
    can_configure: false,
    ...fields,
  };
}

function message(fields: Partial<BotMessage> = {}): BotMessage {
  return {
    id: 7,
    kind: "message",
    status: "needs_review",
    chat_id: "120363043968066561@g.us",
    chat_name: "Отгрузка вагонов",
    sender_id: "998901112233@c.us",
    sender_name: "Джин-Син",
    text: "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 12 вагон\nД1с-28087658-68 тн",
    sent_at: "2026-09-19T09:30:00Z",
    received_at: "2026-09-19T09:30:02Z",
    parsed: { client_name: "ООО OSIYO NAV NIHOL", station: "Раустан", wagons: 12, tons: "816" },
    issues: [
      { code: "product_unknown", message: "Неизвестный код товара «Д1с»", line: 3, subject: "Д1с", order_id: null },
    ],
    draft: "",
    order: null,
    original: null,
    reply: "Принято, на проверке: Неизвестный код товара «Д1с»",
    reply_sent_at: "2026-09-19T09:30:05Z",
    reply_attempts: 0,
    attempts: 0,
    error: "",
    resolved_by_name: "",
    resolved_at: null,
    ...fields,
  };
}

function paged(items: BotMessage[]) {
  return {
    items,
    count: items.length,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    refreshError: "",
    reload: vi.fn(),
    refresh: mocks.refresh,
    loadMore: vi.fn(),
    applyItems: mocks.applyItems,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.permissions = ["bots.view", "bots.manage"];
  mocks.status = status();
  mocks.polling = [];
  mocks.sheetRow = null;
  mocks.paged.mockReturnValue(paged([message()]));
  mocks.get.mockResolvedValue({ data: status() });
});

describe("WhatsApp-бот: журнал", () => {
  it("shows the bot state and the review tab by default, polling quietly", () => {
    render(<WhatsAppBotPage />);

    expect(screen.getByText("Проводит отчёты")).toBeInTheDocument();
    expect(screen.getByText("Подключён")).toBeInTheDocument();
    expect(mocks.paged).toHaveBeenCalledWith("/bots/whatsapp/messages/?status=review", 50);
    expect(screen.getByRole("tab", { name: /На проверке/ })).toHaveAttribute("aria-selected", "true");
    expect(mocks.polling.at(-1)).toEqual({ interval: 15_000, active: true });
    expect(screen.getByText("ООО OSIYO NAV NIHOL · ст. Раустан · 12 вагонов · 816 т")).toBeInTheDocument();
  });

  it("switches tabs by server filter", async () => {
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("tab", { name: "Проведено" }));

    expect(mocks.paged).toHaveBeenLastCalledWith("/bots/whatsapp/messages/?status=applied", 50);
  });

  it("expanded row shows text, reasons and reply; «Провести» opens the report sheet with the message", async () => {
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));

    expect(screen.getByText(/Д1с-28087658-68 тн/)).toBeInTheDocument();
    expect(within(screen.getByRole("status")).getByText("Неизвестный код товара «Д1с»")).toBeInTheDocument();
    expect(screen.getByText("Принято, на проверке: Неизвестный код товара «Д1с»")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Провести/ }));

    expect(mocks.sheet).toHaveBeenCalledWith(
      expect.objectContaining({ api: "/bots/whatsapp/messages/7", initialText: message().text }),
    );
    // Пока человек решает, опрос не перетирает список.
    expect(mocks.polling.at(-1)?.active).toBe(false);

    const applied = message({ status: "applied", order: 41, issues: [] });
    mocks.sheetRow = applied;
    await userEvent.click(screen.getByRole("button", { name: "Провести отчёт" }));

    const update = mocks.applyItems.mock.calls[0][0] as (items: BotMessage[]) => BotMessage[];
    // Проведённое уходит из вкладки «На проверке».
    expect(update([message()])).toEqual([]);
    expect(mocks.get).toHaveBeenCalledWith("/bots/whatsapp/status/");
  });

  it("AI draft is what gets conducted", async () => {
    mocks.paged.mockReturnValue(paged([message({ status: "awaiting_confirmation", draft: "черновик отчёта" })]));
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("черновик отчёта")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Провести/ }));

    expect(mocks.sheet).toHaveBeenCalledWith(expect.objectContaining({ initialText: "черновик отчёта" }));
  });

  it("ignore applies the answer; errors stay in the row", async () => {
    const ignored = message({ status: "ignored", resolved_by_name: "Динара", resolved_at: "2026-09-19T10:00:00Z" });
    mocks.post.mockResolvedValueOnce({ data: ignored });
    render(<WhatsAppBotPage />);
    await userEvent.click(screen.getByRole("button", { expanded: false }));

    await userEvent.click(screen.getByRole("button", { name: /Игнорировать/ }));

    expect(mocks.post).toHaveBeenCalledWith("/bots/whatsapp/messages/7/ignore/");
    const update = mocks.applyItems.mock.calls[0][0] as (items: BotMessage[]) => BotMessage[];
    expect(update([message()])).toEqual([]);

    mocks.post.mockRejectedValueOnce(new Error("Сообщение уже проведено: заказ №41"));
    await userEvent.click(screen.getByRole("button", { name: /Игнорировать/ }));
    expect(await screen.findByText("Сообщение уже проведено: заказ №41")).toBeInTheDocument();
  });

  it("viewer without bots.manage sees no decisions; conducted row links to its order", async () => {
    mocks.status = status({ can_manage: false });
    mocks.paged.mockReturnValue(paged([message({ status: "applied", order: 41, issues: [] })]));
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("button", { expanded: false }));

    expect(screen.queryByRole("button", { name: /Провести/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Игнорировать/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть заказ №41" })).toHaveAttribute(
      "href",
      "/orders/41?back=%2Fmanagement%2Fwhatsapp-bot",
    );
  });

  it("administrator edits settings and the answer replaces the header", async () => {
    mocks.status = status({ can_configure: true });
    const saved = status({ can_configure: true, settings: { ...status().settings, enabled: false } });
    mocks.put.mockResolvedValueOnce({ data: saved });
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    // Дубль вагона — ± дней от даты отчёта, а не «любой повтор за две недели».
    expect(screen.getByLabelText("Дубли вагонов, ± дней")).toHaveValue(3);
    expect(
      screen.getByText("Вагон уже отгружен в пределах ± стольких дней от даты отчёта — на проверку."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Бот проводит отчёты/ }));
    await userEvent.click(screen.getByRole("button", { name: /Склад/ }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(mocks.setStatus).toHaveBeenCalledWith(saved));
    expect(mocks.put).toHaveBeenCalledWith("/bots/whatsapp/settings/", {
      enabled: false,
      allowed_chat_ids: ["120363043968066561@g.us", "120363000000000001@g.us"],
      allowed_sender_ids: ["998901112233@c.us"],
      show_amounts_in_reply: false,
      duplicate_window_days: 3,
      price_tolerance_pct: "15",
    });
  });

  it("settings errors stay inside the modal", async () => {
    mocks.status = status({ can_configure: true });
    mocks.put.mockRejectedValueOnce(new Error("«x» — не номер WhatsApp и не идентификатор чата"));
    render(<WhatsAppBotPage />);

    await userEvent.click(screen.getByRole("button", { name: /Настройки/ }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("«x» — не номер WhatsApp и не идентификатор чата")).toBeInTheDocument();
  });

  it("server switch is explained", () => {
    mocks.status = status({ server_enabled: false });
    render(<WhatsAppBotPage />);

    expect(screen.getByText("Выключен на сервере")).toBeInTheDocument();
  });
});
