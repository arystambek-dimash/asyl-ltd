import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AskEvent, AssistantChat, AssistantMessage, TraceStep } from "@/lib/assistant";
import { apiState } from "@/test-utils/api";

import AssistantPage from "./page";

const mocks = vi.hoisted(() => ({
  chats: [] as AssistantChat[],
  get: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, delete: vi.fn() },
  apiError: (error: Error) => error.message,
}));
vi.mock("@/lib/use-api", () => ({ useApi: () => apiState(mocks.chats) }));
vi.mock("@/lib/assistant", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/assistant")>()),
  askAssistant: mocks.ask,
}));
// В jsdom у графика нет размеров; проверяем переключатель и таблицу, а не SVG.
vi.mock("recharts", () => {
  const Stub = () => null;
  return {
    Bar: Stub,
    BarChart: Stub,
    CartesianGrid: Stub,
    Line: Stub,
    LineChart: Stub,
    ResponsiveContainer: Stub,
    Tooltip: Stub,
    XAxis: Stub,
    YAxis: Stub,
  };
});
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));

function message(fields: Partial<AssistantMessage>): AssistantMessage {
  return {
    id: 1,
    role: "user",
    content: "",
    table: null,
    trace: [],
    stats: {},
    data_map: { tables: [], links: [] },
    error: "",
    created_at: "2026-10-13T10:00:00+05:00",
    ...fields,
  };
}

const SQL_STEP: TraceStep = {
  id: 2,
  kind: "sql",
  status: "running",
  text: "Считаю долг по клиентам",
  sql: "SELECT client, debt FROM debts",
  tables: ["orders_order", "clients_client"],
};
const MAP = {
  tables: ["orders_order", "clients_client"],
  links: [["orders_order", "clients_client"]] as [string, string][],
};

const CHAT: AssistantChat = { id: 7, title: "Долги клиентов", updated_at: "2026-10-13T10:00:00+05:00" };

describe("ИИ-ассистент", () => {
  it("показывает работу вживую: шаги, SQL и карту данных, затем ответ с графиком, скоростью и ходом работы", async () => {
    mocks.chats = [];
    let finish: () => void = () => {};
    mocks.ask.mockImplementation(async (question: string, _chat: number | null, onEvent: (event: AskEvent) => void) => {
      onEvent({ type: "start", chat: CHAT, question: message({ id: 1, content: question }) });
      onEvent({ type: "step", step: { id: 1, kind: "think", status: "done", text: "Разбираю вопрос", ms: 2400 } });
      onEvent({ type: "step", step: SQL_STEP });
      onEvent({ type: "map", ...MAP });
      await new Promise<void>((resolve) => (finish = resolve));
      onEvent({
        type: "reply",
        message: message({
          id: 2,
          role: "assistant",
          content: "Долг клиентов — 1 250 000 ₸.",
          table: {
            columns: ["client", "debt"],
            rows: [["Асем", "1250000.00"]],
            chart: { type: "bar", x: "client", y: "debt" },
          },
          trace: [{ ...SQL_STEP, status: "done", ms: 84, row_count: 1 }],
          stats: { total_ms: 5200, model_calls: 2, sql_queries: 1, input_tokens: 10000, cached_tokens: 8000 },
          data_map: MAP,
        }),
      });
    });
    render(<AssistantPage />);

    await userEvent.click(screen.getByRole("button", { name: /Какой долг у клиентов/ }));

    expect(mocks.ask).toHaveBeenCalledWith(
      "Какой долг у клиентов на сегодня? Топ-10 по сумме",
      null,
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("Ассистент работает")).toBeInTheDocument();
    expect(screen.getByText("Считаю долг по клиентам")).toBeInTheDocument();
    expect(screen.getByText("SELECT client, debt FROM debts")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Затронутые таблицы: orders_order, clients_client/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Остановить" })).toBeInTheDocument();

    await act(async () => finish());

    expect(await screen.findByText("Долг клиентов — 1 250 000 ₸.")).toBeInTheDocument();
    expect(screen.queryByText("Ассистент работает")).not.toBeInTheDocument();
    expect(await screen.findByText(/5,2 с · 2 шага модели · 1 SQL · кэш 80 %/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "График" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Таблица" }));
    expect(screen.getByRole("columnheader", { name: "debt" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Ход работы/ }));
    expect(screen.getByText("Считаю долг по клиентам")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Показать SQL" })).toBeInTheDocument();
  });

  it("следующий вопрос уходит в тот же разговор", async () => {
    mocks.chats = [];
    mocks.ask.mockImplementation(async (question: string, _chat: number | null, onEvent: (event: AskEvent) => void) => {
      onEvent({ type: "start", chat: CHAT, question: message({ id: question.length, content: question }) });
      onEvent({ type: "reply", message: message({ id: question.length + 100, role: "assistant", content: "Ок." }) });
    });
    render(<AssistantPage />);
    const field = screen.getByRole("textbox", { name: "Вопрос ассистенту" });

    await userEvent.type(field, "Сколько заказов?{Enter}");
    await screen.findByText("Сколько заказов?");
    await userEvent.type(field, "А вчера?{Enter}");

    await waitFor(() =>
      expect(mocks.ask).toHaveBeenLastCalledWith("А вчера?", 7, expect.any(Function), expect.any(AbortSignal)),
    );
  });

  it("открывает разговор из истории", async () => {
    mocks.chats = [CHAT];
    mocks.get.mockResolvedValue({
      data: {
        ...CHAT,
        messages: [message({ content: "Есть долги?" }), message({ id: 2, role: "assistant", content: "Нет." })],
      },
    });
    render(<AssistantPage />);

    await userEvent.click(screen.getByRole("button", { name: /^Долги клиентов/ }));

    expect(mocks.get).toHaveBeenCalledWith("/assistant/chats/7/");
    expect(await screen.findByText("Нет.")).toBeInTheDocument();
  });

  it("показывает причину, если вопрос не принят", async () => {
    mocks.chats = [];
    const { AskFailed } = await import("@/lib/assistant");
    mocks.ask.mockRejectedValue(new AskFailed("Лимит вопросов на сегодня исчерпан."));
    render(<AssistantPage />);

    await userEvent.type(screen.getByRole("textbox", { name: "Вопрос ассистенту" }), "Долги?{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("Лимит вопросов на сегодня исчерпан.");
  });
});
