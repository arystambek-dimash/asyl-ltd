import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShippingSegment, ShippingSession, ShippingSessionsPage } from "@/lib/shipping-sessions";
import { CameraShippingSessions } from "./camera-shipping-sessions";

const mocks = vi.hoisted(() => ({
  page: null as ShippingSessionsPage | null,
  error: "",
  errorStatus: null as number | null,
  urls: [] as (string | null)[],
  reload: vi.fn().mockResolvedValue(undefined),
  post: vi.fn(),
  poll: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.urls.push(url);
    return {
      data: url ? mocks.page : null,
      loading: false,
      error: url ? mocks.error : "",
      errorStatus: url ? mocks.errorStatus : null,
      reload: mocks.reload,
      setData: vi.fn(),
    };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.poll }));

/** Local wall-clock time on 10.09.2026: rendered times must not depend on the machine's zone. */
function at(hours: number, minutes: number) {
  return new Date(2026, 8, 10, hours, minutes).toISOString();
}

function segment(overrides: Partial<ShippingSegment> = {}): ShippingSegment {
  return {
    id: 101,
    number: "28055531",
    number_source: "gpt",
    identity_status: "identified",
    identity_error: "",
    started_at: at(21, 4),
    last_counted_at: at(21, 20),
    ended_at: at(21, 20),
    total_bags: 400,
    photo_url: "/fixture/segment-101.jpg",
    photo_taken_at: at(21, 4),
    idle_timeout_seconds: 300,
    can_identify: false,
    ...overrides,
  };
}

function session(overrides: Partial<ShippingSession> = {}): ShippingSession {
  return {
    id: 8,
    camera: "cam2",
    recognition_model: "wagon_number",
    number: "28055531",
    status: "closed",
    total_bags: 609,
    started_at: at(21, 4),
    last_counted_at: at(21, 32),
    ended_at: at(21, 32),
    order_id: null,
    // The server orders colours by count; «Не определён» must still end the row.
    colors: [
      { color: "white", total: 420, percent: 69 },
      { color: "blue", total: 178, percent: 29.2 },
      { color: "unclassified", total: 9, percent: 1.5 },
      { color: "red", total: 2, percent: 0.3 },
    ],
    segments: [
      segment(),
      segment({ id: 102, total_bags: 209, started_at: at(21, 25), last_counted_at: at(21, 32), ended_at: at(21, 32) }),
    ],
    ...overrides,
  };
}

function page(results: ShippingSession[], truncated = false): ShippingSessionsPage {
  return { results, next_cursor: null, truncated };
}

function renderDay(day: string | null = "2026-09-10") {
  return render(<CameraShippingSessions camera="cam2" day={day} today="2026-09-11" />);
}

function colorChips(card: HTMLElement) {
  return within(card)
    .getAllByRole("listitem")
    .map((item) => item.textContent);
}

beforeEach(() => {
  mocks.urls = [];
  mocks.error = "";
  mocks.errorStatus = null;
  mocks.page = page([session()]);
});

describe("CameraShippingSessions", () => {
  it("lists each wagon of the chosen day with its time, bags and every detected colour", () => {
    mocks.page = page([
      session(),
      session({
        id: 7,
        number: "28819852",
        total_bags: 549,
        order_id: 512,
        started_at: at(20, 5),
        last_counted_at: at(20, 41),
        ended_at: at(20, 41),
        colors: [{ color: "blue", total: 549, percent: 100 }],
        segments: [segment({ id: 99, number: "28819852", total_bags: 549 })],
      }),
    ]);
    renderDay();
    expect(mocks.urls).toContain("/cameras/shipping-sessions/?camera=cam2&day=2026-09-10");
    const region = screen.getByRole("region", { name: "Сессии отгрузки" });
    expect(region).toHaveTextContent("10.09.2026");
    expect(region).toHaveTextContent("2 вагона · 1 158 меш.");
    const [first, second] = within(region).getAllByRole("article");
    expect(first).toHaveAccessibleName("Вагон 28055531");
    expect(first).toHaveTextContent("21:04–21:32 · 2 отрезка");
    expect(first).toHaveTextContent("609 меш.");
    expect(colorChips(first)).toEqual(["Белый 420", "Синий 178", "Красный 2", "Не определён 9"]);
    expect(second).toHaveAccessibleName("Вагон 28819852");
    expect(second).toHaveTextContent("20:05–20:41 · Заказ #512");
    expect(second).not.toHaveTextContent("отрез");
    expect(colorChips(second)).toEqual(["Синий 549"]);
  });

  it.each([
    ["wagon_number", "wagon_number", "2 вагона"],
    ["vehicle_number", "vehicle_number", "2 машины"],
    ["wagon_number", "vehicle_number", "2 сессии"],
  ] as const)("counts %s + %s transport as «%s»", (firstModel, secondModel, label) => {
    mocks.page = page([session({ recognition_model: firstModel }), session({ id: 7, recognition_model: secondModel })]);
    renderDay();
    expect(screen.getByRole("region", { name: "Сессии отгрузки" })).toHaveTextContent(`${label} · 1 218 меш.`);
  });

  it("keeps a wagon's segments, frames and invoices behind its row", async () => {
    const user = userEvent.setup();
    renderDay();
    const card = screen.getByRole("article", { name: "Вагон 28055531" });
    expect(within(card).queryByRole("article", { name: "Отрезок 101" })).toBeNull();
    await user.click(within(card).getByRole("button", { expanded: false }));
    const first = within(card).getByRole("article", { name: "Отрезок 101" });
    const second = within(card).getByRole("article", { name: "Отрезок 102" });
    expect(within(first).getByText("Номер: GPT")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Накладная отрезка" })).toHaveAttribute(
      "href",
      "/monoblock/shipping-segments/101/print",
    );
    expect(within(second).getByRole("link", { name: "Накладная отрезка" })).toHaveAttribute(
      "href",
      "/monoblock/shipping-segments/102/print",
    );
  });

  it.each([
    ["wagon_checksum_invalid", "ИИ прочитал номер, но контрольная цифра не совпала."],
    ["number_invalid_format", "ИИ прочитал символы, но они не соответствуют формату номера."],
    ["transport_type_mismatch", "Тип транспорта на фото не совпадает с настройкой камеры номера."],
    ["openai_authentication_failed", "Проверка через OpenAI не авторизована. Обратитесь к администратору."],
    ["openai_rate_limited", "Достигнут лимит запросов OpenAI."],
    ["openai_unavailable", "OpenAI временно недоступен."],
    ["openai_request_rejected", "OpenAI отклонил запрос распознавания. Обратитесь к администратору."],
    ["openai_invalid_response", "OpenAI вернул некорректный результат распознавания."],
    ["openai_output_limit", "OpenAI не завершил ответ: достигнут лимит длины."],
    ["openai_refused", "OpenAI не смог обработать этот кадр."],
    ["openai_incomplete", "OpenAI не завершил распознавание номера."],
  ])("opens an unnumbered wagon, explains %s and offers a manual number", (identity_error, message) => {
    mocks.page = page([
      session({
        number: "",
        segments: [
          segment({
            number: "",
            number_source: "",
            identity_status: "unidentified",
            identity_error,
            can_identify: true,
          }),
        ],
      }),
    ]);
    renderDay();
    const card = screen.getByRole("article", { name: "Транспорт без номера" });
    expect(within(card).getByRole("button", { expanded: true })).toHaveTextContent("Без номера");
    expect(within(card).getByText(`${message} Подсчёт мешков сохранён.`)).toBeInTheDocument();
    expect(within(card).getByText("609 меш.")).toBeInTheDocument();
    expect(within(card).getByRole("textbox", { name: "Указать номер отрезка #101" })).toBeInTheDocument();
  });

  it("keeps photo failures visible and submits a manual number only for that segment", async () => {
    const user = userEvent.setup();
    const unknown = segment({
      number: "",
      number_source: "",
      identity_status: "unidentified",
      identity_error: "photo_unavailable",
      can_identify: true,
    });
    mocks.page = page([session({ number: "", total_bags: 400, segments: [unknown] })]);
    mocks.post.mockResolvedValue({ data: { ...unknown, number: "28055531", number_source: "manual" } });
    renderDay();
    fireEvent.error(screen.getByRole("img", { name: "Кадр номера отрезка 101" }));
    expect(screen.getByText("Кадр недоступен")).toBeInTheDocument();
    expect(screen.getByText(/Кадр номера недоступен. Подсчёт мешков сохранён/)).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Указать номер отрезка #101" }), " 28055531 ");
    await user.click(screen.getByRole("button", { name: "Сохранить номер" }));
    expect(mocks.post).toHaveBeenCalledWith("/cameras/shipping-segments/101/identify/", { number: "28055531" });
    expect(mocks.reload).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Номер 28055531 сохранён");
  });

  it("preserves the unknown record and typed number when the server refuses identification", async () => {
    const user = userEvent.setup();
    mocks.page = page([
      session({
        number: "",
        segments: [segment({ number: "", can_identify: true, identity_status: "unidentified" })],
      }),
    ]);
    mocks.post.mockRejectedValue(new Error("Номер уже изменён другим сотрудником"));
    renderDay();
    await user.type(screen.getByRole("textbox"), "28055531");
    await user.click(screen.getByRole("button", { name: "Сохранить номер" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Номер уже изменён");
    expect(screen.getByRole("textbox")).toHaveValue("28055531");
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps unidentified segments read-only without the load permission", () => {
    mocks.page = page([
      session({
        number: "",
        segments: [segment({ number: "", identity_status: "unidentified", can_identify: false })],
      }),
    ]);
    renderDay();
    expect(screen.getByRole("article", { name: "Отрезок 101" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows an API outage beside the wagons already on screen instead of an empty day", () => {
    mocks.error = "Сервер недоступен";
    renderDay();
    expect(screen.getByRole("alert")).toHaveTextContent("Сервер недоступен");
    expect(screen.getByRole("article", { name: "Вагон 28055531" })).toHaveTextContent("609 меш.");
    expect(screen.queryByText("За этот день отгрузок нет")).toBeNull();
  });

  it("does not download the same saved photo again when polling renews its signed URL", async () => {
    const user = userEvent.setup();
    mocks.page = page([session({ segments: [segment({ photo_url: "/fixture/image.jpg?token=first" })] })]);
    const { rerender } = renderDay();
    await user.click(screen.getByRole("button", { expanded: false }));
    const initialUrl = screen.getByRole("img").getAttribute("src");
    mocks.page = page([session({ segments: [segment({ photo_url: "/fixture/image.jpg?token=second" })] })]);
    rerender(<CameraShippingSessions camera="cam2" day="2026-09-10" today="2026-09-11" />);
    expect(screen.getByRole("img")).toHaveAttribute("src", initialUrl);
    expect(screen.getByRole("link", { name: "Открыть кадр отрезка 101" })).toHaveAttribute(
      "href",
      "/fixture/image.jpg?token=second",
    );
  });

  it("refreshes the list only while it shows today", () => {
    const { unmount } = render(<CameraShippingSessions camera="cam2" day="2026-09-11" today="2026-09-11" />);
    expect(mocks.poll).toHaveBeenLastCalledWith(mocks.reload, 3000, true);
    unmount();
    renderDay("2026-09-10");
    expect(mocks.poll).toHaveBeenLastCalledWith(mocks.reload, 3000, false);
  });

  it("asks for a day on the chart instead of loading a whole period", () => {
    renderDay(null);
    expect(screen.getByRole("region", { name: "Сессии отгрузки" })).toHaveTextContent(
      "Выберите день на графике «Учтено по дням»",
    );
    expect(mocks.urls.filter(Boolean)).toEqual([]);
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("explains a day without shipments", () => {
    mocks.page = page([]);
    renderDay();
    expect(screen.getByText("За этот день отгрузок нет")).toBeInTheDocument();
  });

  it("says when the day's list is capped", () => {
    mocks.page = page([session(), session({ id: 7, number: "28819852" })], true);
    renderDay();
    expect(screen.getByText("Показаны последние 2 сессии дня.")).toBeInTheDocument();
  });
});
