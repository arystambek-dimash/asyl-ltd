import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it, vi } from "vitest";
import {
  AskFailed,
  askAssistant,
  formatAssistantCell,
  isNumericCell,
  tableParts,
  upsertStep,
  type AskEvent,
  type TraceStep,
} from "@/lib/assistant";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { post: mocks.post },
}));

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("askAssistant", () => {
  it("parses NDJSON events split across chunks and sends the question without a global timeout", async () => {
    const start = { type: "start", chat: { id: 7, title: "Долги", updated_at: "" }, question: { id: 1 } };
    const reply = { type: "reply", message: { id: 2, content: "Долгов нет." } };
    const body = `${JSON.stringify(start)}\n{"type":"step","text":"Счит`;
    mocks.post.mockResolvedValue({ data: streamOf(body, `аю долг"}\n${JSON.stringify(reply)}`) });
    const events: AskEvent[] = [];

    await askAssistant("Долги?", 7, (event) => events.push(event));

    expect(mocks.post).toHaveBeenCalledWith(
      "/assistant/ask/",
      { question: "Долги?", chat: 7 },
      expect.objectContaining({ adapter: "fetch", responseType: "stream", timeout: 0 }),
    );
    expect(events.map((event) => event.type)).toEqual(["start", "step", "reply"]);
    expect(events[1]).toEqual({ type: "step", text: "Считаю долг" });
  });

  it("turns a JSON error body that arrived as a stream into the server's message", async () => {
    const rejection = new AxiosError("failed");
    rejection.response = {
      status: 429,
      statusText: "",
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
      data: streamOf(JSON.stringify({ detail: "Лимит вопросов на сегодня исчерпан.", code: "assistant_daily_limit" })),
    };
    mocks.post.mockRejectedValue(rejection);

    const failure: unknown = await askAssistant("Ещё?", null, () => {}).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(AskFailed);
    expect((failure as AskFailed).message).toBe("Лимит вопросов на сегодня исчерпан.");
  });
});

describe("formatAssistantCell", () => {
  it("groups numbers, keeps record ids and formats dates", () => {
    expect(formatAssistantCell("1250000.50", "debt")).toBe(formatAssistantCell(1250000.5, "sum"));
    expect(formatAssistantCell(1234, "order_id")).toBe("1234");
    expect(formatAssistantCell("2026-10-13", "day")).toBe("13.10.2026");
    expect(formatAssistantCell("2026-10-13 14:05", "at")).toBe("13.10.2026 14:05");
    expect(formatAssistantCell(null, "x")).toBe("—");
    expect(formatAssistantCell(true, "x")).toBe("да");
    expect(formatAssistantCell("Мука", "name")).toBe("Мука");
    expect(isNumericCell("12.5")).toBe(true);
    expect(isNumericCell("12 мешков")).toBe(false);
  });
});

describe("ход работы", () => {
  it("шаг с тем же id заменяется готовым, новый встаёт в конец", () => {
    const running: TraceStep = { id: 1, kind: "sql", status: "running", text: "Считаю" };
    const think: TraceStep = { id: 2, kind: "think", status: "running", text: "Думаю" };

    const steps = upsertStep(upsertStep([], running), think);
    const done = upsertStep(steps, { ...running, status: "done", ms: 84, row_count: 3 });

    expect(done.map((step) => [step.id, step.status])).toEqual([
      [1, "done"],
      [2, "running"],
    ]);
  });

  it("подпись узла карты делит таблицу на приложение и модель", () => {
    expect(tableParts("orders_orderitem")).toEqual({ app: "orders", model: "orderitem" });
    expect(tableParts("plain")).toEqual({ app: "", model: "plain" });
  });
});
